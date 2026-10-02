"""

PDF image compressor.

Re-encodes the raster images inside PDFs as JPEG (optionally downsampling
them), then saves a garbage-collected PDF. Concurrency strategies:

  file   : several PDFs are processed at the same time, one per worker process
  image  : PDFs are processed one after another, and the images of the current
           PDF are recompressed in parallel

The strategy is chosen automatically: "file" when there are at least 2x as many
PDFs as workers, otherwise "image".

Output: -o/--output defaults to "." which means the source folder itself, so PDFs
are rewritten in place. Give any other path to write compressed copies there instead.

All progress/trace state lives in the main process, so there are no shared
manager objects or locks.

"""

import argparse
import hashlib
import io
import json
import math
import queue
import re
import shutil
import signal
import sys
import time
from datetime import datetime
from multiprocessing import Pool, Process, Queue, cpu_count
from pathlib import Path

import pymupdf
from PIL import Image

DEFAULT_QUALITY = 60
DEFAULT_MAX_DPI = 160

# Fixed settings (formerly command-line options), with the reasoning for each value:
FIXED = {
    "garbage": 4,            # MuPDF garbage-collection level 4 also merges identical streams; costs little
    "skip_threshold": 2048,  # images under 2 KB can't save enough to justify a decode + encode
    "optimize": True,        # Huffman-optimised JPEG: ~5% smaller for a negligible CPU cost
    "clean": False,          # sanitising content streams slows the save and rarely shrinks the file
    "dedupe": True,          # identical image copies are encoded once; falls back to "unique" if unsure
    "min_bpp": 1.0,          # already below 1 bit/pixel: re-encoding gains little and adds generation loss
    "resave": False,         # PDFs where no image changed are kept as-is instead of re-saved
}


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _is_hidden(rel_path: Path) -> bool:
    """True if any component of a *relative* path starts with '.'."""
    return any(part.startswith(".") for part in rel_path.parts)


def _is_inside(path: Path, folder: Path) -> bool:
    try:
        path.relative_to(folder)
        return True
    except ValueError:
        return False


def _fmt_size(n: int) -> str:
    return f"{n / 1024:.1f}KB" if n < 1024 * 1024 else f"{n / 1024 / 1024:.2f}MB"


def _bar(done: int, total: int, width: int = 15) -> str:
    filled = int(width * done / total) if total else 0
    return "=" * filled + "-" * (width - filled)


def _temp_path_for(target: Path) -> Path:
    # Hidden, non-.pdf name: never picked up by a later scan as an input file.
    return target.with_name(f".{target.name}.compress-tmp")


# --------------------------------------------------------------------------- #
# Image recompression (runs in worker processes)
# --------------------------------------------------------------------------- #
def _int_key(doc, xref: int, key: str) -> int:
    t, v = doc.xref_get_key(xref, key)
    return int(v) if t == "int" else 0


def _scale_for(w: int, h: int, dim_pt: float, max_dpi: int) -> float:
    """Downsampling factor (< 1) needed to reach max_dpi, or 1.0 if none is needed/possible."""
    if max_dpi and dim_pt > 0 and w and h:
        dpi = max(w, h) / (dim_pt / 72.0)
        if dpi > max_dpi * 1.1:
            return max_dpi / dpi
    return 1.0


def _open_jpeg_reduced(raw: bytes, size):
    """Decode a JPEG stream straight at reduced size (DCT scaling, much faster than
    full decode + resize). Returns None for anything but plain RGB/gray JPEGs."""
    try:
        im = Image.open(io.BytesIO(raw))
        if im.mode not in ("RGB", "L"):
            im.close()
            return None
        im.draft(im.mode, size)
        im.load()
        return im
    except Exception:
        return None


def _process_image(doc, xref: int, dim_pt: float, cfg: dict):
    """Return new JPEG bytes for the image at `xref`, or None to leave it alone."""
    try:
        raw = doc.xref_stream_raw(xref)
        raw_len = len(raw)
        if raw_len < cfg["skip_threshold"]:
            return None

        # Things we can't safely re-encode as a plain JPEG.
        if doc.xref_get_key(xref, "ImageMask")[1] == "true":
            return None
        if doc.xref_get_key(xref, "BitsPerComponent")[1] == "1":  # bilevel (CCITT/JBIG2 scans)
            return None
        if doc.xref_get_key(xref, "Mask")[0] != "null":            # colour-key / stencil mask
            return None
        if doc.xref_get_key(xref, "Decode")[0] != "null":          # inverted / remapped samples
            return None

        w, h = _int_key(doc, xref, "Width"), _int_key(doc, xref, "Height")
        scale = _scale_for(w, h, dim_pt, cfg["max_dpi"])

        # Already tightly compressed and no resize needed: re-encoding gains little
        # (and costs a decode + encode, plus generation loss).
        if scale == 1.0 and w and h and cfg["min_bpp"] and raw_len * 8 / (w * h) < cfg["min_bpp"]:
            return None

        img = None
        if scale <= 0.5 and doc.xref_get_key(xref, "Filter")[1] == "/DCTDecode":
            img = _open_jpeg_reduced(raw, (max(1, round(w * scale)), max(1, round(h * scale))))

        if img is None:
            pix = pymupdf.Pixmap(doc, xref)
            if pix.alpha or pix.colorspace is None:
                return None
            if pix.colorspace.n not in (1, 3):                     # CMYK etc. -> RGB via MuPDF
                pix = pymupdf.Pixmap(pymupdf.csRGB, pix)
            mode = "L" if pix.n == 1 else "RGB"
            img = Image.frombuffer(mode, (pix.width, pix.height), pix.samples, "raw", mode, pix.stride, 1)
            if not (w and h):
                w, h = img.size
                scale = _scale_for(w, h, dim_pt, cfg["max_dpi"])

        if scale < 1.0:
            target = (max(1, round(w * scale)), max(1, round(h * scale)))
            if img.size != target:
                img = img.resize(target, Image.Resampling.LANCZOS)

        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=cfg["quality"], optimize=cfg["optimize"])
        data = buf.getvalue()
        return data if len(data) < raw_len else None
    except Exception:
        return None


def _worker_init():
    """Workers ignore Ctrl+C/SIGTERM; the main process coordinates shutdown."""
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    signal.signal(signal.SIGTERM, signal.SIG_IGN)


def _image_chunk_job(task):
    """Pool worker: open the PDF once, recompress a chunk of images."""
    path, chunk, cfg = task
    out = []
    with pymupdf.open(path) as doc:
        for xref, dim_pt in chunk:
            out.append((xref, _process_image(doc, xref, dim_pt, cfg)))
    return out


def _collect_images(doc, want_size: bool) -> dict:
    """xref -> {"page": first page using it, "dim_pt": largest displayed size in points}."""
    images = {}
    for pno, page in enumerate(doc):
        for info in page.get_images(full=True):
            xref, smask = info[0], info[1]
            if smask:  # images with a soft mask would lose transparency
                continue
            entry = images.setdefault(xref, {"page": pno, "dim_pt": 0.0})
            if want_size:
                try:
                    for r in page.get_image_rects(xref):
                        entry["dim_pt"] = max(entry["dim_pt"], r.width, r.height)
                except Exception:
                    pass
    return images


def _colorspace_sig(doc, xref: int):
    try:
        v = doc.xref_get_key(xref, "ColorSpace")[1]
        sig = [re.sub(r"\d+ 0 R", "@", v)]
        for n in re.findall(r"(\d+) 0 R", v):
            n = int(n)
            raw = doc.xref_stream_raw(n) if doc.xref_is_stream(n) else b""
            body = re.sub(r"\d+ 0 R", "@", doc.xref_object(n))
            sig.append(hashlib.blake2b(raw + body.encode(), digest_size=16).digest().hex())
        return tuple(sig)
    except Exception:
        return ("unique", xref)


def _group_duplicates(doc, xrefs) -> list:
    """Group image objects that are byte-identical (same stream, filter, colour space...)."""
    def meta(x):
        g = lambda k: doc.xref_get_key(x, k)[1]
        length = g("Length") if doc.xref_get_key(x, "Length")[0] == "int" else "?"
        return (g("Width"), g("Height"), g("BitsPerComponent"), g("Filter"), length,
                g("DecodeParms"), g("Decode"), g("Mask"), g("ImageMask"))

    buckets = {}
    for x in xrefs:
        buckets.setdefault(meta(x), []).append(x)

    groups = []
    for members in buckets.values():
        if len(members) == 1:
            groups.append(members)
            continue
        by_content = {}
        for x in members:
            try:
                key = (hashlib.blake2b(doc.xref_stream_raw(x), digest_size=16).digest(), _colorspace_sig(doc, x))
            except Exception:
                key = ("unique", x)
            by_content.setdefault(key, []).append(x)
        groups.extend(by_content.values())
    return groups


# --------------------------------------------------------------------------- #
# One PDF, start to finish (with automatic repair fallback)
# --------------------------------------------------------------------------- #
def process_one(src_file: Path, rel: Path, ctx: dict, pool=None, progress=None) -> dict:
    cfg = ctx["cfg"]
    clone = ctx["mode"] == "clone"
    t0 = time.time()
    if clone:
        target = ctx.get("target_file") or ctx["dst_dir"] / rel
    else:
        target = src_file
    tmp = _temp_path_for(target)
    repaired_tmp = None

    res = {"rel": str(rel), "status": "FAILED", "in_size": 0, "out_size": 0,
           "images_total": 0, "images_replaced": 0, "duplicates_skipped": 0,
           "duration": 0.0, "error": None, "note": None}
    unchanged = False
    try:
        in_size = src_file.stat().st_size
        res["in_size"] = res["out_size"] = in_size

        target.parent.mkdir(parents=True, exist_ok=True)

        doc = None
        try:
            doc = pymupdf.open(src_file)
        except Exception as open_err:
            try:
                repaired_tmp = src_file.with_name(f".{src_file.name}.repair-tmp")
                with pymupdf.open(src_file) as broken_doc:
                    broken_doc.save(repaired_tmp, garbage=4, deflate=True, clean=True)
                doc = pymupdf.open(repaired_tmp)
                res["note"] = "repaired corrupt PDF structure"
            except Exception as repair_err:
                raise RuntimeError(f"failed to open or repair PDF: {open_err}")

        with doc:
            if doc.needs_pass:
                raise RuntimeError("encrypted PDF (password required)")

            images = _collect_images(doc, want_size=cfg["max_dpi"] > 0)
            groups = _group_duplicates(doc, list(images)) if cfg["dedupe"] else [[x] for x in images]
            members = {g[0]: g for g in groups}
            items = []
            for g in groups:
                dims = [images[x]["dim_pt"] for x in g]
                items.append((g[0], 0.0 if min(dims) <= 0 else max(dims)))
            total = len(items)
            res["images_total"] = len(images)
            res["duplicates_skipped"] = len(images) - total
            garbage = max(cfg["garbage"], 4) if res["duplicates_skipped"] else cfg["garbage"]
            if progress:
                progress(0, total)

            if pool is not None and total:
                if repaired_tmp is not None:
                    results = ((x, _process_image(doc, x, d, cfg)) for x, d in items)
                else:
                    size = max(1, min(8, math.ceil(total / (cfg["workers"] * 4))))
                    tasks = [(str(src_file), items[i:i + size], cfg) for i in range(0, total, size)]
                    results = (r for batch in pool.imap_unordered(_image_chunk_job, tasks) for r in batch)
            else:
                results = ((x, _process_image(doc, x, d, cfg)) for x, d in items)

            done = 0
            for xref, new_bytes in results:
                done += 1
                if new_bytes is not None:
                    for x in members[xref]:
                        try:
                            doc[images[x]["page"]].replace_image(x, stream=new_bytes)
                            res["images_replaced"] += 1
                        except Exception:
                            pass
                if progress:
                    progress(done, total)

            unchanged = not res["images_replaced"] and not res["duplicates_skipped"] and not cfg["resave"]
            if not unchanged:
                doc.save(tmp, garbage=garbage, deflate=True, clean=cfg["clean"], use_objstms=1)

        if not unchanged and tmp.stat().st_size < in_size:
            tmp.replace(target)
            res["status"], res["out_size"] = "REDUCED", target.stat().st_size
        else:
            tmp.unlink(missing_ok=True)
            if unchanged and not res["note"]:
                res["note"] = "no image changes"
            if clone:
                shutil.copy2(src_file, target)
                res["status"] = "KEPT_ORIGINAL"
            else:
                res["status"] = "SKIPPED_OVERWRITE"
    except Exception as e:
        tmp.unlink(missing_ok=True)
        res["status"], res["error"] = "FAILED", str(e)
        if clone:
            try:
                shutil.copy2(src_file, target)
            except Exception:
                pass
    finally:
        tmp.unlink(missing_ok=True)
        if repaired_tmp is not None:
            repaired_tmp.unlink(missing_ok=True)

    res["duration"] = round(time.time() - t0, 2)
    res["timestamp"] = datetime.now().strftime("%H:%M:%S")
    return res


# --------------------------------------------------------------------------- #
# Trace file + terminal dashboard (main process only)
# --------------------------------------------------------------------------- #
class Trace:
    def __init__(self, path):
        self.path = path
        self.queued, self.active, self.completed, self.non_pdf = {}, {}, {}, {}

    def flush(self):
        """Write current trace dictionary snapshot directly to disk."""
        if not self.path:
            return
        data = {
            "queued_pdf": list(self.queued.values()),
            "active_pdf": list(self.active.values()),
            "completed_pdf": list(self.completed.values()),
            "non_pdf": list(self.non_pdf.values()),
        }
        try:
            tmp = self.path.with_name(self.path.name + ".tmp")
            tmp.write_text(json.dumps(data, indent=4), encoding="utf-8")
            tmp.replace(self.path)
        except OSError:
            pass

    def finish(self, res):
        self.queued.pop(res["rel"], None)
        entry = {
            "name": res["rel"], "images_total": res["images_total"],
            "images_replaced": res["images_replaced"],
            "duplicates_skipped": res["duplicates_skipped"],
            "before_size": round(res["in_size"] / (1024 * 1024), 2),
            "after_size": round(res["out_size"] / (1024 * 1024), 2),
            "duration": res["duration"], "status": res["status"],
        }
        if res["error"]:
            entry["error"] = res["error"]
        self.completed[res["rel"]] = entry
        
        # Only write trace file when a PDF is saved or processed
        if res["status"] in ("REDUCED", "KEPT_ORIGINAL", "SKIPPED_OVERWRITE", "FAILED"):
            self.flush()


class Dashboard:
    """N live status lines that stay at the bottom while log lines scroll above."""

    def __init__(self, n_lines: int):
        rows = shutil.get_terminal_size((100, 30)).lines
        self.tty = sys.stdout.isatty() and n_lines <= rows - 3
        self.lines = [""] * n_lines
        self.drawn = False
        self.last = 0.0

    def _erase(self):
        if self.drawn:
            sys.stdout.write(f"\033[{len(self.lines) + 1}A\033[J")
            self.drawn = False

    def _draw(self):
        width = shutil.get_terminal_size((100, 30)).columns - 1
        separator = "─" * min(width, 55)
        output = separator + "\n" + "\n".join(l[:width] for l in self.lines) + "\n"
        sys.stdout.write(output)
        sys.stdout.flush()
        self.drawn = True
        self.last = time.time()

    def set(self, i: int, text: str):
        self.lines[i] = text
        if self.tty and time.time() - self.last > 0.1:
            self._erase()
            self._draw()

    def log(self, text: str):
        if self.tty:
            self._erase()
        print(text)
        if self.tty:
            self._draw()

    def close(self):
        if self.tty:
            self._erase()


def _format_result(res: dict) -> str:
    ts = f"[{res['timestamp']}]"
    if res["status"] == "REDUCED":
        pct = (res["in_size"] - res["out_size"]) / res["in_size"] * 100
        return (f"{ts} [REDUCED] {res['rel']} ({_fmt_size(res['in_size'])} -> {_fmt_size(res['out_size'])} "
                f"| -{pct:.1f}%) {res['images_replaced']}/{res['images_total']} images"
                + (f" ({res['duplicates_skipped']} duplicate(s) encoded once)" if res["duplicates_skipped"] else "")
                + f", {res['duration']}s")
    if res["status"] == "FAILED":
        return f"{ts} [FAILED]  {res['rel']}: {res['error']}"
    return f"{ts} [KEPT]    {res['rel']} ({res.get('note') or 'no size benefit'})"


# --------------------------------------------------------------------------- #
# File-level concurrency: worker process
# --------------------------------------------------------------------------- #
def _file_worker(worker_id, task_q, result_q, ctx):
    _worker_init()
    while True:
        idx = None
        rel_str = ""
        try:
            task = task_q.get()
            if task is None:
                break
            idx, path = task
            rel = path.relative_to(ctx["src_dir"])
            rel_str = str(rel)
            result_q.put(("START", worker_id, idx, rel_str))
            last = [0.0]

            def progress(done, total):
                now = time.time()
                if done == total or now - last[0] > 0.2:
                    last[0] = now
                    result_q.put(("PROGRESS", worker_id, idx, rel_str, done, total))

            res = process_one(path, rel, ctx, pool=None, progress=progress)
            result_q.put(("DONE", worker_id, idx, res))
        except Exception as e:
            if idx is not None:
                err_res = {
                    "rel": rel_str, "status": "FAILED", "in_size": 0, "out_size": 0,
                    "images_total": 0, "images_replaced": 0, "duplicates_skipped": 0,
                    "duration": 0.0, "error": str(e), "note": None,
                    "timestamp": datetime.now().strftime("%H:%M:%S")
                }
                try:
                    result_q.put(("DONE", worker_id, idx, err_res))
                except Exception:
                    pass


# --------------------------------------------------------------------------- #
# Driver
# --------------------------------------------------------------------------- #
def run(args):
    source = Path(args.source).resolve()
    single_file = source.is_file()
    if single_file:
        if source.suffix.lower() != ".pdf":
            sys.exit(f"Error: Source file is not a PDF: {args.source}")
        src_dir = source.parent
    elif source.is_dir():
        src_dir = source
    else:
        sys.exit(f"Error: Source not found: {args.source}")

    out = src_dir if args.output == "." else Path(args.output).resolve()
    if single_file:
        out_file = out if out.suffix.lower() == ".pdf" else out / source.name
        mode = "rewrite" if out_file == source else "clone"
    else:
        mode = "rewrite" if out == src_dir else "clone"

    dst_dir = src_dir
    target_file = None  # single-file mode: exact output path
    if mode == "clone":
        if single_file:
            target_file = out_file
            dst_dir = target_file.parent
        else:
            dst_dir = out
        dst_dir.mkdir(parents=True, exist_ok=True)

    workers = max(1, args.workers)
    cfg = {**FIXED, "quality": args.quality, "max_dpi": args.max_dpi, "workers": workers}
    ctx = {"src_dir": src_dir, "dst_dir": dst_dir, "mode": mode, "cfg": cfg, "target_file": target_file}

    trace = Trace(dst_dir / ".trace.json")

    # Confirmation prompt displaying all explicit & implicit settings
    print("=" * 60)
    print(" EXECUTION PARAMETERS & CONFIRMATION ")
    print("=" * 60)
    print(f" Source Path        : {source}")
    print(f" Execution Mode     : {mode.upper()} " + ("(In-place Overwrite)" if mode == "rewrite" else "(Clone to Output)"))
    print(f" Output Path        : {target_file or dst_dir}")
    print(f" Overwrite Existing : {args.overwrite}")
    print(f" JPEG Quality       : {args.quality}")
    print(f" Max DPI            : {args.max_dpi or 'Disabled (Off)'}")
    print(f" Worker Processes   : {workers}")
    print(f" Trace File         : {trace.path}")
    print("-" * 60)
    print(" Fixed Engine Parameters:")
    for k, v in FIXED.items():
        print(f"   - {k:<18}: {v}")
    print("=" * 60)

    confirm = input("Proceed with compression? [y/N]: ").strip().lower()
    if confirm not in ("y", "yes"):
        print("Operation cancelled by user.")
        sys.exit(0)
    print()

    # ---- Phase 1: scan ----
    pdfs, others = [], []
    skipped_existing = 0
    candidates = [source] if single_file else src_dir.rglob("*")
    for p in candidates:
        if not p.is_file():
            continue
        if p.name.endswith((".compress-tmp", ".trace.json", ".trace.json.tmp", ".repair-tmp")):
            continue
        if not single_file and dst_dir != src_dir and _is_inside(p, dst_dir):
            continue
        rel = p.relative_to(src_dir)

        # Skip files that already exist if overwrite is False (clone mode only)
        if mode == "clone" and not args.overwrite and (target_file or dst_dir / rel).exists():
            skipped_existing += 1
            continue

        # An explicitly named PDF is always processed, even if its name starts with '.'
        if p.suffix.lower() == ".pdf" and (single_file or not _is_hidden(rel)):
            pdfs.append(p)
        else:
            others.append(p)

    if skipped_existing > 0:
        print(f"Skipped {skipped_existing} existing file(s) in destination (overwrite=False).")

    stats = {"reduced": 0, "kept": 0, "failed": 0, "non_pdf": 0, "in_bytes": 0, "out_bytes": 0}

    for f in others:
        rel = f.relative_to(src_dir)
        try:
            size = f.stat().st_size
            if mode == "clone":
                target_non_pdf = dst_dir / rel
                if args.overwrite or not target_non_pdf.exists():
                    target_non_pdf.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(f, target_non_pdf)
                status = "COPIED_NON_PDF"
                stats["non_pdf"] += 1
            else:
                status = "IGNORED_NON_PDF"
        except OSError:
            continue
        trace.non_pdf[str(rel)] = {"name": str(rel), "size": round(size / (1024 * 1024), 2), "status": status}

    total = len(pdfs)
    if total == 0:
        print("No visible PDF files found to process." if not single_file else "Nothing to do.")
        return

    def _size(p):
        try:
            return p.stat().st_size
        except OSError:
            return 0

    multi_mode = "file" if total >= 2 * workers else "image"
    print(f"Concurrency: {total} PDF(s), {workers} worker(s) -> {multi_mode} mode")
    pdfs.sort(key=_size, reverse=(multi_mode == "file"))
    for i, f in enumerate(pdfs, 1):
        rel = str(f.relative_to(src_dir))
        trace.queued[rel] = {"name": rel, "size": round(_size(f) / (1024 * 1024), 2), "sort_index": i}

    failures = []
    stop = {"n": 0}

    def _on_signal(signum, frame):
        stop["n"] += 1
        if stop["n"] > 1:
            trace.flush()
            raise KeyboardInterrupt

    signal.signal(signal.SIGINT, _on_signal)
    signal.signal(signal.SIGTERM, _on_signal)

    def record(res):
        stats["in_bytes"] += res["in_size"]
        stats["out_bytes"] += res["out_size"]
        if res["status"] == "REDUCED":
            stats["reduced"] += 1
        elif res["status"] == "FAILED":
            stats["failed"] += 1
            failures.append((res["rel"], res["error"]))
        else:
            stats["kept"] += 1
        trace.finish(res)

    start = time.time()
    forced = False

    # ---- Phase 2 -------------------------------------------------------
    try:
        if multi_mode == "file":
            workers = min(workers, total)
            print(f"Phase 2 (file mode): {total} PDFs, {workers} worker processes\n")
            task_q, result_q = Queue(), Queue()
            for idx, f in enumerate(pdfs, 1):
                task_q.put((idx, f))
            for _ in range(workers):
                task_q.put(None)

            procs = [Process(target=_file_worker, args=(w, task_q, result_q, ctx), daemon=True) for w in range(workers)]
            for p in procs:
                p.start()

            dash = Dashboard(workers + 2)
            for w in range(workers):
                dash.set(w, f"[Worker {w + 1:02d}] idle")
            active = {}
            completed = 0
            try:
                drained = False
                while completed < total:
                    if stop["n"] and not drained:
                        dash.log("Stop requested: finishing files in progress (Ctrl+C again to abort now)...")
                        try:
                            while True:
                                task_q.get_nowait()
                        except queue.Empty:
                            pass
                        for _ in range(workers):
                            task_q.put(None)
                        drained = True
                    try:
                        msg = result_q.get(timeout=1.0)
                    except queue.Empty:
                        for w, p in enumerate(procs):
                            if not p.is_alive() and p.exitcode not in (0, None) and w in active:
                                idx, rel = active.pop(w)
                                src = src_dir / rel
                                res = {"rel": rel, "status": "FAILED", "in_size": _size(src), "out_size": _size(src),
                                       "images_total": 0, "images_replaced": 0, "duplicates_skipped": 0, "duration": 0.0,
                                       "error": f"worker crashed (exit code {p.exitcode})",
                                       "timestamp": datetime.now().strftime("%H:%M:%S")}
                                if mode == "clone":
                                    try:
                                        (dst_dir / rel).parent.mkdir(parents=True, exist_ok=True)
                                        shutil.copy2(src, dst_dir / rel)
                                    except OSError:
                                        pass
                                record(res)
                                completed += 1
                                dash.log(_format_result(res))
                                dash.set(w, f"[Worker {w + 1:02d}] dead")
                        if not any(p.is_alive() for p in procs) and result_q.empty():
                            break
                        continue

                    kind, w = msg[0], msg[1]
                    label = f"[Worker {w + 1:02d}] [{msg[2]:03d}/{total:03d}]"
                    if kind == "START":
                        active[w] = (msg[2], msg[3])
                        trace.queued.pop(msg[3], None)
                        trace.active[w] = {"name": msg[3], "worker_id": w, "images_done": 0, "images_total": 0}
                        dash.set(w, f"{label} {msg[3]} [starting]")
                    elif kind == "PROGRESS":
                        _, _, _, rel, done, tot = msg
                        if w in trace.active:
                            trace.active[w].update(images_done=done, images_total=tot)
                        dash.set(w, f"{label} {rel} [{_bar(done, tot)}] {done}/{tot} img")
                    elif kind == "DONE":
                        res = msg[3]
                        active.pop(w, None)
                        trace.active.pop(w, None)
                        record(res)
                        completed += 1
                        dash.log(_format_result(res))
                        dash.set(w, f"[Worker {w + 1:02d}] idle")
                    dash.set(workers, f"Overall: {completed}/{total} PDFs completed")
            finally:
                dash.close()
                for p in procs:
                    if p.is_alive():
                        p.terminate()
                for p in procs:
                    p.join(timeout=3)
                for _w, (_idx, _rel) in active.items():
                    _t = (dst_dir / _rel) if mode == "clone" else (src_dir / _rel)
                    _temp_path_for(_t).unlink(missing_ok=True)

        else:
            print(f"Phase 2 (image mode): {total} PDFs one at a time, {workers} image workers\n")
            dash = Dashboard(1)
            try:
                with Pool(processes=workers, initializer=_worker_init) as pool:
                    for idx, f in enumerate(pdfs, 1):
                        if stop["n"]:
                            dash.log("Stop requested: skipping remaining files.")
                            break
                        rel = f.relative_to(src_dir)
                        trace.queued.pop(str(rel), None)
                        trace.active[0] = {"name": str(rel), "worker_id": 0, "images_done": 0, "images_total": 0}
                        t0 = time.time()

                        def progress(done, tot, rel=rel, idx=idx, t0=t0):
                            if str(rel) in trace.active.get(0, {}).get("name", ""):
                                trace.active[0].update(images_done=done, images_total=tot)
                            speed = done / (time.time() - t0) if time.time() > t0 else 0
                            dash.set(0, f"[{idx:03d}/{total:03d}] {rel} [{_bar(done, tot)}] {done}/{tot} img | {speed:.1f} img/s")

                        res = process_one(f, rel, ctx, pool=pool, progress=progress)
                        trace.active.pop(0, None)
                        record(res)
                        dash.log(_format_result(res))
            finally:
                dash.close()

    except KeyboardInterrupt:
        forced = True
        print("\nAborted immediately (interrupt received).")

    # Clean up any leftover temporary files safely on exit
    for tmp_file in (dst_dir.glob if single_file else dst_dir.rglob)(".*.compress-tmp"):
        try:
            tmp_file.unlink(missing_ok=True)
        except OSError:
            pass

    trace.queued.clear()
    trace.active.clear()
    trace.flush()  # Flush final state upon exit/termination

    print("\n" + "=" * 55)
    print(" EXECUTION SUMMARY ")
    print("=" * 55)
    print(f" PDFs Reduced       : {stats['reduced']}")
    print(f" PDFs Kept Original : {stats['kept']}")
    print(f" Other Files Copied : {stats['non_pdf']}")
    print(f" Failed Operations  : {stats['failed']}")
    print(f" Space Saved        : {(stats['in_bytes'] - stats['out_bytes']) / (1024 * 1024):.2f} MB")
    not_processed = total - stats["reduced"] - stats["kept"] - stats["failed"]
    interrupted = forced or stop["n"] > 0
    if interrupted or not_processed:
        print(f" Not Processed      : {not_processed} of {total} PDFs" + (" (stopped early)" if interrupted else ""))
    print(f" Total Duration     : {time.time() - start:.2f} seconds")
    print(f" Trace Log          : {trace.path}")
    print("=" * 55)

    if failures:
        note = " (originals were copied unchanged)" if mode == "clone" else " (originals left untouched)"
        print(f"\nFAILED FILES ({len(failures)}){note}:")
        for rel, err in sorted(failures):
            print(f"  - {rel}: {err}")
    if interrupted:
        sys.exit(130)


def main():

    parser = argparse.ArgumentParser(
        description="Shrink the images inside PDFs (JPEG re-encoding, optional downsampling)."
    )
    
    parser.add_argument(
        "-s", "--source", default=".",
        help="Source directory, or a single PDF file (default: current directory)"
    )
    parser.add_argument(
        "-o", "--output", default=".",
        help="Output directory (or file path for a single PDF). Default: current "
             "directory. If it resolves to the source location, PDFs are rewritten in place."
    )
    parser.add_argument(
        "-q", "--quality", type=int, default=DEFAULT_QUALITY,
        help=f"JPEG quality 1-95 (default: {DEFAULT_QUALITY})"
    )
    parser.add_argument(
        "-w", "--workers", type=int, default=cpu_count(),
        help="Worker processes (default: all CPUs)"
    )

    parser.add_argument(
        "--max-dpi", type=int, default=DEFAULT_MAX_DPI,
        help=f"Downsample images above this effective DPI; 0 disables (default: {DEFAULT_MAX_DPI})"
    )
    parser.add_argument(
        "--overwrite", action="store_true",
        help="Re-compress and overwrite files that already exist in the output directory"
    )

    args = parser.parse_args()

    if not 1 <= args.quality <= 95:
        parser.error("--quality must be between 1 and 95")
    if args.max_dpi < 0:
        parser.error("--max-dpi must be >= 0")

    run(args)


if __name__ == "__main__":
    main()