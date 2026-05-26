const { contextBridge} = require('electron');
const { ipcRenderer } = require('electron/renderer');
const fs = require('fs');

contextBridge.exposeInMainWorld('archivesAPI', {
    list: () => ipcRenderer.invoke('archives:getAll'),
    add: () => ipcRenderer.invoke('archives:add'),
    addByPath: path => ipcRenderer.invoke('archives:addByPath', path),
    remove: id => ipcRenderer.invoke('archives:remove', id)
});

contextBridge.exposeInMainWorld('electronAPI', {
    getUserDataPath: () => ipcRenderer.sendSync('getUserDataPath'),
    readJson: (p) => JSON.parse(fs.readFileSync(p, 'utf8'))
});