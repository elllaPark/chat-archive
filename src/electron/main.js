const { app, BrowserWindow, ipcMain, dialog } = require('electron');
const { spawn } = require('child_process');
const path = require('path');
const fs = require('fs');
const crypto = require('crypto');

const USER_DATA = app.getPath('userData');
const CONFIG_PATH = path.join(USER_DATA, 'config.json');
const ARCHIVES_DIR = path.join(USER_DATA, 'archives');

// MARK: - Config helpers

// Reads config.json. if it doesn't exist, return {archives:[]]}
function loadConfig() {
    try {
        return JSON.parse(fs.readFileSync(CONFIG_PATH, 'utf-8'));
    } catch {
        return {
            archives: []
        };
    }
}

//ensures the directory ezists, then writs the JSON back to disk
function saveConfig(cfg) {
    fs.mkdirSync(path.dirname(CONFIG_PATH), {recursive: true});
    fs.writeFileSync(CONFIG_PATH, JSON.stringify(cfg, null, 2));
}

// MD5
function hashPath(absPath) {
    return crypto.createHash('md5').update(absPath).digest('hex').slice(0, 16);
}

//MARK: -parser
function runPythonParser(inputTxt, outputJson) {
    return new Promise((resolve, reject) => {
        const py = spawn('python3', [
            path.join(__dirname, '..', 'parser', 'main.py'),
            inputTxt,
            outputJson
        ]);
        py.stderr.on('data', d => console.error('[python]', d.toString()));
        py.on('error', reject);
        py.on('close', code =>
            code === 0 ? resolve() : reject(new Error(`parser failed (${code})`))
        );
    });
}

async function indexArchive(folder) {
    const archiveId = hashPath(folder);
    const cacheDir = path.join(ARCHIVES_DIR, archiveId);
    fs.mkdirSync(cacheDir, { recursive: true });

    const txtFiles = fs.readdirSync(folder).filter(f => f.endsWith('.txt')).map(f => path.join(folder, f));

    const allMsgs = [];
    
    for (const txt of txtFiles) {
        const tmpJson = path.join(cacheDir, path.basename(txt) + '.tmp.json');
        await runPythonParser(txt, tmpJson);

        const msgs = JSON.parse(fs.readFileSync(tmpJson, 'utf8'))
        allMsgs.push(...msgs);

        fs.unlinkSync(tmpJson);
    }

    allMsgs.sort((a, b) => new Date(a.timestamp.replace(' ','T')).getTime() - new Date(b.timestamp.replace(' ','T')).getTime());
    fs.writeFileSync(path.join(cacheDir, 'index.json'), JSON.stringify(allMsgs));

    return archiveId;
}

//MARK:-ipc
ipcMain.handle('archives:getAll', () => loadConfig().archives);

ipcMain.handle('archives:add', async () => {
    const { canceled, filePaths } = await dialog.showOpenDialog({
        properties: ['openDirectory'],
        title: 'Select chat archive folder'
    });
    console.log('[archives:add] dialog result:', { canceled, filePaths });
    if (canceled || !filePaths.length) { 
        const cfg = loadConfig();
        console.log('[archives:add] returning existing list, size=', cfg.archives.length);
        return cfg.archives;
    }

    const folder = filePaths[0];
    const archiveId = hashPath(folder);
    const cfg = loadConfig()
    try {
    if (!cfg.archives.find(a => a.archiveId === archiveId)) {
        //parse and cache
        await indexArchive(folder);

        cfg.archives.push({
            archiveId,
            path:folder,
            label: path.basename(folder),
            addedAt: new Date().toISOString()
        });
        saveConfig(cfg)
        console.log('[archives:add] new archive added, total =', cfg.archives.length);
    }
    } catch (err) {
        console.error('[archives:add] indexArchive failed:', err);
    }

    return cfg.archives;
});

ipcMain.handle('archives:addByPath', async (_e, folderPath) => {
    if (!folderPath) return loadConfig().archives;
    const archiveId = hashPath(folderPath);
    const cfg = loadConfig()

    if (!cfg.archives.find(a => a.archiveID === archiveId)) {
        //parse and cache
        await indexArchive(folderPath);

        cfg.archives.push({
            archiveId,
            path: folderPath,
            label: path.basename(folderPath),
            addedAt: new Date().toISOString()
        });
        saveConfig(cfg)
    }

    return cfg.archives;
});

ipcMain.handle('archives:remove', (_e, archiveId) => {
    const cfg = loadConfig();
    cfg.archives = cfg.archives.filter(a => a.archiveId !== archiveId);
    saveConfig(cfg);

    fs.rmSync(path.join(ARCHIVES_DIR, archiveId), { recursive: true, force: true });
    return cfg.archives;
});

ipcMain.handle('dialog:openDirectory', async () => {
    const { canceled, filePaths } = await dialog.showOpenDialog({
        properties: ['openDirectory'],
        title: 'Select chat archive folder'
    });
    return canceled ? null : filePaths[0];
})


//MARK:- create window
const isMac = process.platform === 'darwin';
const isDev = process.env.NODE_ENV !== 'production'; 

function createMainWindow() {
    const window = new BrowserWindow({
        title: 'Chat archive',
        width: isDev ? 1000 : 500,
        height: 800,
        webPreferences: {
            contextIsolation: true,
            nodeIntegration: true,
            sandbox: false,
            preload: path.join(__dirname,'preload.js')
        }
    });

    if (isDev) {
        window.webContents.openDevTools();
    }

    window.loadFile(path.join(__dirname, '..', 'renderer', 'index.html'));
}

app.whenReady().then(() => {
    createMainWindow();

    app.on('activate', () => {
        if (BrowserWindow.getAllWindows().length === 0) {
            createMainWindow();
        };
    });
});

app.on('window-all-closed', () => {
    if (!isMac) {
        app.quit();
    };
});