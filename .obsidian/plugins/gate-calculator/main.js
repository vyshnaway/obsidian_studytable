const { Plugin, setIcon, PluginSettingTab, Setting } = require('obsidian');

const DEFAULT_SETTINGS = {
    theme: 'system', // 'system', 'light', 'dark'
    defaultAngleMode: 'deg', // 'deg', 'rad'
    showRibbonIcon: true,
    showStatusIcon: true,
    showInCommandPalette: true,
    autoCloseBrackets: false // Advanced setting (Default OFF)
};

// --- Robust Math Evaluator ---
const safeEval = (str, isDegree) => {
    const scope = {
        pi: Math.PI,
        e: Math.E,
        sin: (x) => Math.sin(x * (isDegree ? Math.PI / 180 : 1)),
        cos: (x) => Math.cos(x * (isDegree ? Math.PI / 180 : 1)),
        tan: (x) => Math.tan(x * (isDegree ? Math.PI / 180 : 1)),
        asin: (x) => Math.asin(x) * (isDegree ? 180 / Math.PI : 1),
        acos: (x) => Math.acos(x) * (isDegree ? 180 / Math.PI : 1),
        atan: (x) => Math.atan(x) * (isDegree ? 180 / Math.PI : 1),
        sinh: Math.sinh, cosh: Math.cosh, tanh: Math.tanh,
        asinh: Math.asinh, acosh: Math.acosh, atanh: Math.atanh,
        ln: Math.log,
        log: Math.log10,
        log10: Math.log10,
        log2: (x) => Math.log2(x),
        logBase: (val, base) => Math.log(val) / Math.log(base),
        exp: Math.exp,
        sqrt: Math.sqrt,
        cbrt: Math.cbrt,
        root: (x, n) => Math.pow(x, 1/n),
        pow: Math.pow,
        abs: Math.abs,
        fact: (n) => {
            if (n < 0) return NaN;
            if (Math.abs(n - Math.round(n)) > 1e-9) return NaN; 
            n = Math.round(n);
            if (n === 0 || n === 1) return 1;
            let r = 1;
            for (let i = 2; i <= n; i++) r *= i;
            return r;
        }
    };

    let cleanStr = str
        .replace(/π/g, "pi")
        .replace(/Math\.PI/g, "pi")
        .replace(/\^/g, "**") 
        .replace(/mod/g, "%");

    // Safe Implicit Multiplication (Fixed for log2 and log10)
    // 1. Number followed by letter (e.g., 2pi -> 2*pi, 5sin -> 5*sin)
    cleanStr = cleanStr.replace(/(\d)\s*(?=[a-zA-Z])/g, '$1*');
    // 2. Number followed by '(' BUT explicitly ignoring numbers inside function names (like log2) via Negative Lookbehind
    cleanStr = cleanStr.replace(/(?<![a-zA-Z])(\d+)\s*(?=\()/g, '$1*');
    // 3. Closing parenthesis followed by letter, number, or '('
    cleanStr = cleanStr.replace(/\)\s*(?=[a-zA-Z\d\(])/g, ')*');

    const keys = Object.keys(scope);
    const values = Object.values(scope);
    
    try {
        const func = new Function(...keys, `"use strict"; return (${cleanStr})`);
        return func(...values);
    } catch (err) {
        throw new Error("Syntax Error");
    }
};

class FloatingWidget {
    constructor(plugin, title) {
        this.plugin = plugin;
        this.app = plugin.app;
        this.visible = false;
        this.grabOffsets = { top: 0, left: 0 };
        
        // 1. Main Wrapper
        this.el = document.createElement("div");
        this.el.addClass("gc-widget", "gc-hidden");
        this.el.setAttribute("tabindex", "-1"); 
        
        this.applyTheme(this.plugin.settings.theme);

        this.el.addEventListener("mousedown", () => {
            this.el.focus();
        });
        
        // 2. Draggable Top Bar
        const top = this.el.createDiv({ cls: "gc-widget-top" });
        
        top.createSpan({ text: title, cls: "gc-widget-title" });
        
        const controls = top.createDiv({ cls: "gc-widget-controls" });
        
        this.minimizeBtn = controls.createEl("button", { cls: "gc-widget-btn" });
        setIcon(this.minimizeBtn, "minus");
        
        this.closeBtn = controls.createEl("button", { cls: "gc-widget-btn" });
        setIcon(this.closeBtn, "x");
        
        // --- CUSTOM POINTER DRAG LOGIC ---
        let isDragging = false;

        top.addEventListener("pointerdown", (e) => {
            if (e.target.closest('.gc-widget-btn')) return;
            
            isDragging = true;
            top.setPointerCapture(e.pointerId);
            
            const rect = this.el.getBoundingClientRect();
            this.grabOffsets.top = e.clientY - rect.top;
            this.grabOffsets.left = e.clientX - rect.left;
            
            e.preventDefault();
            e.stopPropagation();
        });
        
        top.addEventListener("pointermove", (e) => {
            if (!isDragging) return;
            
            let newX = e.clientX - this.grabOffsets.left;
            let newY = e.clientY - this.grabOffsets.top;
            
            const rect = this.el.getBoundingClientRect();
            const maxX = window.innerWidth - rect.width;
            const maxY = window.innerHeight - rect.height;
            
            newX = Math.max(0, Math.min(newX, maxX));
            newY = Math.max(30, Math.min(newY, maxY));
            
            this.el.style.left = `${newX}px`;
            this.el.style.top = `${newY}px`;
            this.el.style.right = "unset";
            this.el.style.bottom = "unset";
            
            e.preventDefault();
            e.stopPropagation();
        });
        
        const endDrag = (e) => {
            if (!isDragging) return;
            isDragging = false;
            top.releasePointerCapture(e.pointerId);
        };
        
        top.addEventListener("pointerup", endDrag);
        top.addEventListener("pointercancel", endDrag);
        
        // Button Listeners
        this.minimizeBtn.addEventListener("click", (e) => {
            e.stopPropagation();
            this.hide(); 
        });
        
        this.closeBtn.addEventListener("click", (e) => {
            e.stopPropagation();
            if (this.onClose) this.onClose(); 
        });

        // 3. Isolated Content Container
        this.contentEl = this.el.createDiv({ cls: "gc-widget-content" });

        this.app.workspace.onLayoutReady(() => {
            this.app.workspace.containerEl.appendChild(this.el);
        });
    }

    applyTheme(themeStr) {
        this.el.removeClass('force-light', 'force-dark');
        if (themeStr === 'light') {
            this.el.addClass('force-light');
        } else if (themeStr === 'dark') {
            this.el.addClass('force-dark');
        }
    }

    isVisible() { return this.visible; }

    show() {
        this.el.removeClass("gc-widget-hide", "gc-widget-close", "gc-hidden");
        setTimeout(() => {
            this.el.addClass("gc-widget-show");
            this.el.focus();
        }, 1);
        this.visible = true;
    }

    hide() {
        this.el.removeClass("gc-widget-show", "gc-widget-close");
        setTimeout(() => this.el.addClass("gc-widget-hide"), 1);
        setTimeout(() => this.el.addClass("gc-hidden"), 150); 
        this.visible = false;
    }

    close() {
        this.el.removeClass("gc-widget-show", "gc-widget-hide");
        setTimeout(() => this.el.addClass("gc-widget-close"), 1);
        setTimeout(() => this.el.addClass("gc-hidden"), 100); 
        this.visible = false;
    }

    toggle() {
        if (this.visible) this.hide();
        else this.show();
    }

    destroy() {
        this.el.remove();
    }
}

class GateCalculator {
    constructor(widget, plugin) {
        this.widget = widget;
        this.plugin = plugin;
        this.parentEl = widget.contentEl; 
        this.expression = "";
        this.memory = 0;
        this.isDegree = this.plugin.settings.defaultAngleMode === 'deg';
        this.isResultState = false;
        
        this.handleKeyDown = this.handleKeyDown.bind(this);
        this.render();
    }

    setAngleMode(mode) {
        this.isDegree = (mode === 'deg');
        if (this.degInput && this.radInput) {
            this.degInput.checked = this.isDegree;
            this.radInput.checked = !this.isDegree;
        }
    }

    render() {
        this.parentEl.empty(); 
        const body = this.parentEl.createDiv({ cls: "gate-calc-body" });

        const screen = body.createDiv({ cls: "gate-calc-screen" });
        this.exprEl = screen.createDiv({ cls: "gate-calc-expression", text: "" });
        this.resEl = screen.createDiv({ cls: "gate-calc-result", text: "0" });

        const topRow = body.createDiv({ cls: "gate-calc-top-row" });
        const leftControls = topRow.createDiv({ cls: "gate-calc-radio-group" });
        const idSuffix = Math.random().toString(36).substr(2,5); 
        
        // Deg Radio
        const lblDeg = leftControls.createEl("label");
        this.degInput = lblDeg.createEl("input");
        this.degInput.type = "radio";
        this.degInput.name = "gate_mode_" + idSuffix;
        this.degInput.value = "deg";
        this.degInput.checked = this.isDegree;
        lblDeg.createSpan({ text: "Deg" });
        this.degInput.onchange = () => { if(this.degInput.checked) this.isDegree = true; this.widget.el.focus(); };

        // Rad Radio
        const lblRad = leftControls.createEl("label");
        this.radInput = lblRad.createEl("input");
        this.radInput.type = "radio";
        this.radInput.name = "gate_mode_" + idSuffix;
        this.radInput.value = "rad";
        this.radInput.checked = !this.isDegree;
        lblRad.createSpan({ text: "Rad" });
        this.radInput.onchange = () => { if(this.radInput.checked) this.isDegree = false; this.widget.el.focus(); };

        const memDiv = topRow.createDiv({ cls: "gate-mem-buttons" });
        ["MC", "MR", "MS", "M+", "M-"].forEach(m => {
            const b = memDiv.createEl("button", { cls: "gate-btn btn-mem", text: m });
            b.onclick = () => { this.handleMemory(m); this.widget.el.focus(); };
        });

        const grid = body.createDiv({ cls: "gate-calc-grid" });
        
        // 5 Rows x 11 Columns Perfectly Grouped Layout
        const rows = [
            // Row 1: Trig + Hyp + Utility
            [
                { t: "sin", f: "sin(" }, { t: "cos", f: "cos(" }, { t: "tan", f: "tan(" }, { t: "sinh", f: "sinh(" }, { t: "cosh", f: "cosh(" }, { t: "tanh", f: "tanh(" }, 
                { t: "(", f: "(" }, { t: ")", f: ")" }, { t: "←", c: "btn-red", a: "back" }, { t: "C", c: "btn-red", a: "clear" }, { t: "+/-", a: "neg" }
            ],
            // Row 2: Inverses + Top Numpad
            [
                { t: "sin⁻¹", f: "asin(" }, { t: "cos⁻¹", f: "acos(" }, { t: "tan⁻¹", f: "atan(" }, { t: "sinh⁻¹", f: "asinh(" }, { t: "cosh⁻¹", f: "acosh(" }, { t: "tanh⁻¹", f: "atanh(" }, 
                { t: "7", f: "7" }, { t: "8", f: "8" }, { t: "9", f: "9" }, { t: "/", f: "/" }, { t: "%", f: "/100" }
            ],
            // Row 3: Powers/Exp + Mid Numpad
            [
                { t: "x²", f: "^2" }, { t: "x³", f: "^3" }, { t: "xʸ", f: "^" }, { t: "eˣ", f: "exp(" }, { t: "10ˣ", f: "pow(10," }, { t: "e", f: "e" }, 
                { t: "4", f: "4" }, { t: "5", f: "5" }, { t: "6", f: "6" }, { t: "*", f: "*" }, { t: "1/x", a: "recip" }
            ],
            // Row 4: Roots/Logs + Bot Numpad
            [
                { t: "√", f: "sqrt(" }, { t: "∛", f: "cbrt(" }, { t: "ʸ√x", f: "root(" }, { t: "ln", f: "ln(" }, { t: "log", f: "log(" }, { t: "log₂", f: "log2(" }, 
                { t: "1", f: "1" }, { t: "2", f: "2" }, { t: "3", f: "3" }, { t: "-", f: "-" }, { t: "=", c: "btn-green span-row-2", a: "eval" }
            ],
            // Row 5: Constants/Misc + Bot Row Numpad
            [
                { t: "π", f: "π" }, { t: "n!", f: "fact(" }, { t: "|x|", f: "abs(" }, { t: "logᵧx", f: "logBase(" }, { t: ",", f: "," }, { t: "mod", f: "mod" }, 
                { t: "0", f: "0", s: "span-2" }, { t: ".", f: "." }, { t: "+", f: "+" }
            ]
        ];

        rows.forEach(row => {
            row.forEach(btn => {
                const b = grid.createEl("button", { cls: `gate-btn ${btn.c || ""}`, text: btn.t });
                if (btn.s) b.addClass(btn.s);
                if (btn.t === "=") b.setAttribute("style", "grid-row: span 2; height: 100%;");
                if (btn.s === "span-2") b.setAttribute("style", "grid-column: span 2; width: 100%;");

                b.onclick = (e) => {
                    e.preventDefault();
                    this.widget.el.focus();
                    if (btn.a) {
                        switch(btn.a) {
                            case "clear": this.clear(); break;
                            case "back": this.backspace(); break;
                            case "eval": this.calculate(); break;
                            case "neg": this.negate(); break;
                            case "recip": this.insert("^(-1)"); break;
                        }
                    } else {
                        this.handleInput(btn.f);
                    }
                };
            });
        });
    }

    handleKeyDown(e) {
        if (!this.widget.isVisible()) return;
        if (!this.widget.el.contains(document.activeElement)) return;

        const key = e.key;
        if (key === 'Escape') { this.widget.hide(); return; }
        if (key === 'Enter') { e.preventDefault(); return this.calculate(); }
        if (key === 'Backspace') { return this.backspace(); }
        if (key === 'Delete') return this.clear();
        if (/[0-9]/.test(key)) return this.handleInput(key);
        if (['+', '-', '*', '/', '(', ')', '.', '%', '^', '!', ','].includes(key)) return this.handleInput(key);
        if (/^[a-z]$/.test(key)) return this.handleInput(key);
    }

    handleInput(val) {
        const isOperator = ["+", "-", "*", "/", "^", "%", "mod", ")", ","].some(op => val.includes(op));
        if (this.isResultState) {
            if (isOperator) { this.isResultState = false; } 
            else { this.expression = ""; this.isResultState = false; }
        }
        this.expression += val;
        this.updateScreen();
    }
    insert(val) { this.handleInput(val); }
    clear() { this.expression = ""; this.isResultState = false; this.resEl.innerText = "0"; this.updateScreen(); }
    backspace() {
        if (this.isResultState) { this.clear(); } 
        else { this.expression = this.expression.toString().slice(0, -1); this.updateScreen(); }
    }
    negate() {
        if (this.isResultState) { this.expression = `-(${this.expression})`; this.isResultState = false; } 
        else { this.expression += "-"; }
        this.updateScreen();
    }
    handleMemory(action) {
        let val = 0;
        if (this.isResultState) { val = parseFloat(this.resEl.innerText); } 
        else if (this.expression.length > 0) { try { val = safeEval(this.expression, this.isDegree); } catch(e) { val = 0; } }
        if (isNaN(val)) val = 0;

        switch(action) {
            case "MC": this.memory = 0; break;
            case "MS": this.memory = val; break;
            case "MR": 
                if (this.isResultState) { this.expression = ""; this.isResultState = false; }
                this.expression += this.memory.toString(); 
                this.updateScreen();
                break;
            case "M+": this.memory += val; break;
            case "M-": this.memory -= val; break;
        }
    }
    calculate() {
        try {
            if (!this.expression) return;
            
            // --- NEW FEATURE: Auto-Close Parentheses ---
            if (this.plugin.settings.autoCloseBrackets) {
                const openCount = (this.expression.match(/\(/g) || []).length;
                const closeCount = (this.expression.match(/\)/g) || []).length;
                if (openCount > closeCount) {
                    const missing = openCount - closeCount;
                    this.expression += ")".repeat(missing);
                    // Explicitly visually update the screen so the user sees the brackets added
                    this.exprEl.innerText = this.expression;
                }
            }
            
            const res = safeEval(this.expression, this.isDegree);
            let resStr = "";
            
            if (Number.isNaN(res)) resStr = "NaN";
            else if (!Number.isFinite(res)) resStr = "Infinity";
            else {
                if (Math.abs(res - Math.round(res)) < 1e-9) resStr = Math.round(res).toString();
                else resStr = parseFloat(res.toPrecision(10)).toString(); 
            }
            
            this.resEl.innerText = resStr;
            this.expression = resStr;
            this.isResultState = true;
        } catch (e) {
            this.resEl.innerText = "Error";
        }
    }
    updateScreen() {
        this.exprEl.innerText = this.expression;
        this.exprEl.scrollLeft = this.exprEl.scrollWidth;
        if (!this.isResultState) this.resEl.innerText = "";
    }
}

class GateCalculatorSettingTab extends PluginSettingTab {
    constructor(app, plugin) {
        super(app, plugin);
        this.plugin = plugin;
    }

    display() {
        const { containerEl } = this;
        containerEl.empty();

        new Setting(containerEl)
            .setName('Tutorial Video')
            .setDesc('Watch a quick guide on how to use the GATE Calculator.')
            .addButton(btn => btn
                .setButtonText('Watch Video')
                .setCta()
                .onClick(() => {
                    window.open('https://www.youtube.com/@zettelforgate', '_blank');
                }));

        containerEl.createEl('h2', { text: 'GATE Calculator Settings' });

        new Setting(containerEl)
            .setName('Theme')
            .setDesc('Choose the color theme of the calculator.')
            .addDropdown(drop => drop
                .addOption('system', 'Adapt to System')
                .addOption('light', 'Light Mode')
                .addOption('dark', 'Dark Mode')
                .setValue(this.plugin.settings.theme)
                .onChange(async (value) => {
                    this.plugin.settings.theme = value;
                    this.plugin.widget.applyTheme(value);
                    await this.plugin.saveSettings();
                }));

        new Setting(containerEl)
            .setName('Default Angle Mode')
            .setDesc('Initial state for trigonometric calculations.')
            .addDropdown(drop => drop
                .addOption('deg', 'Degrees')
                .addOption('rad', 'Radians')
                .setValue(this.plugin.settings.defaultAngleMode)
                .onChange(async (value) => {
                    this.plugin.settings.defaultAngleMode = value;
                    this.plugin.calculator.setAngleMode(value);
                    await this.plugin.saveSettings();
                }));
                
        new Setting(containerEl)
            .setName('Auto-close Parentheses')
            .setDesc('Automatically append missing closing brackets ")" when evaluating. (Off by default for strict GATE exam practice).')
            .addToggle(toggle => toggle
                .setValue(this.plugin.settings.autoCloseBrackets)
                .onChange(async (value) => {
                    this.plugin.settings.autoCloseBrackets = value;
                    await this.plugin.saveSettings();
                }));

        new Setting(containerEl)
            .setName('Show Ribbon Icon')
            .setDesc('Display the calculator icon in the left side ribbon.')
            .addToggle(toggle => toggle
                .setValue(this.plugin.settings.showRibbonIcon)
                .onChange(async (value) => {
                    this.plugin.settings.showRibbonIcon = value;
                    this.plugin.refreshRibbonIcon(); 
                    await this.plugin.saveSettings();
                }));

        new Setting(containerEl)
            .setName('Show Status Bar Icon')
            .setDesc('Display the calculator icon in the bottom right status bar.')
            .addToggle(toggle => toggle
                .setValue(this.plugin.settings.showStatusIcon)
                .onChange(async (value) => {
                    this.plugin.settings.showStatusIcon = value;
                    this.plugin.refreshStatusBarIcon(); 
                    await this.plugin.saveSettings();
                }));

        new Setting(containerEl)
            .setName('Show in Command Palette')
            .setDesc('Allow "Toggle Calculator" to appear in the command palette. (Ctrl/Cmd + P)')
            .addToggle(toggle => toggle
                .setValue(this.plugin.settings.showInCommandPalette)
                .onChange(async (value) => {
                    this.plugin.settings.showInCommandPalette = value;
                    await this.plugin.saveSettings();
                }));

        new Setting(containerEl)
            .setName('Shortcuts / Hotkeys')
            .setDesc('Assign a quick keyboard shortcut to toggle the calculator.')
            .addButton(btn => btn
                .setButtonText('Configure Hotkeys')
                .onClick(() => {
                    const tab = this.app.setting.openTabById('hotkeys');
                    if (tab && tab.setQuery) {
                        tab.setQuery("Toggle Calculator");
                    }
                }));
    }
}

module.exports = class GateCalculatorPlugin extends Plugin {
    async onload() {
        await this.loadSettings();

        this.addSettingTab(new GateCalculatorSettingTab(this.app, this));

        this.widget = new FloatingWidget(this, "Scientific Calculator");
        this.calculator = new GateCalculator(this.widget, this);
        
        this.widget.onClose = () => {
            this.widget.close();     
        };

        this.refreshRibbonIcon();
        this.refreshStatusBarIcon();

        this.addCommand({
            id: 'toggle-gate-calculator-widget',
            name: 'Toggle Calculator',
            checkCallback: (checking) => {
                if (checking) return this.settings.showInCommandPalette;
                this.widget.toggle();
                return true; 
            }
        });

        this.addCommand({
            id: 'force-toggle-gate-calculator',
            name: 'Toggle Calculator (Hotkey override)',
            callback: () => {
                this.widget.toggle();
            }
        });

        document.addEventListener("keydown", this.calculator.handleKeyDown);
    }

    async loadSettings() {
        this.settings = Object.assign({}, DEFAULT_SETTINGS, await this.loadData());
    }

    async saveSettings() {
        await this.saveData(this.settings);
    }

    refreshRibbonIcon() {
        if (this.settings.showRibbonIcon) {
            if (!this.ribbonIconEl) {
                this.ribbonIconEl = this.addRibbonIcon('calculator', 'Toggle GATE Calculator', () => {
                    this.widget.toggle();
                });
            }
        } else {
            if (this.ribbonIconEl) {
                this.ribbonIconEl.remove();
                this.ribbonIconEl = null;
            }
        }
    }

    refreshStatusBarIcon() {
        if (this.settings.showStatusIcon) {
            if (!this.statusBarIconEl) {
                this.statusBarIconEl = this.addStatusBarItem();
                this.statusBarIconWrapper = this.statusBarIconEl.createEl("span", { cls: "gc-statusbar-icon" });
                setIcon(this.statusBarIconWrapper, "calculator");
                this.statusBarIconWrapper.addEventListener("click", () => this.widget.toggle());
            }
        } else {
            if (this.statusBarIconEl) {
                this.statusBarIconEl.remove();
                this.statusBarIconEl = null;
                this.statusBarIconWrapper = null;
            }
        }
    }

    onunload() {
        document.removeEventListener("keydown", this.calculator.handleKeyDown);
        this.widget.destroy();
    }
};

/* nosourcemap */