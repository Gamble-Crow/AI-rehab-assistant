const { app, BrowserWindow } = require('electron')
const path = require('path')

function createWindow() {
  const win = new BrowserWindow({
    width: 1280,
    height: 800,
    minWidth: 1024,
    minHeight: 600,
    title: 'PhụcHồi',
    webPreferences: {
      nodeIntegration: false,
    }
  })

  // Mở thẳng file app.html — không cần server
  win.loadFile('app.html')
}

app.whenReady().then(createWindow)

app.on('window-all-closed', () => {
  app.quit()
})
