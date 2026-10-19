<%*
const { exec } = require('child_process');
const util = require('util');
const execAsync = util.promisify(exec);

// Path to the current vault root folder
const vaultPath = app.vault.adapter.getBasePath();

try {
  // Executes 'code .' targeting the vault root directory
  await execAsync(`code "${vaultPath}"`);
  new Notice('Opened vault in VS Code');
} catch (error) {
  console.error('Failed to open VS Code:', error);
  new Notice(`Error opening VS Code: ${error.message}`);
}

tR = "";
-%>