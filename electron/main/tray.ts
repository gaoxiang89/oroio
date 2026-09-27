import { Tray, Menu, nativeImage, app, NativeImage, BrowserWindow } from 'electron';
import * as path from 'path';
import { createMainWindow } from './index';
import { useKey, maskKey, getKeyList, type KeyInfo } from './keyManager';

function notifyWebContents(): void {
  BrowserWindow.getAllWindows().forEach(win => {
    win.webContents.send('keys-updated');
  });
}

let tray: Tray | null = null;

function getTrayIcon(): NativeImage {
  const iconPath = app.isPackaged
    ? path.join(process.resourcesPath, 'assets', 'trayTemplate.png')
    : path.join(__dirname, '..', '..', 'assets', 'trayTemplate.png');
  
  const icon = nativeImage.createFromPath(iconPath);
  icon.setTemplateImage(true);
  return icon;
}

function getProgressBar(percent: number, width: number = 10): string {
  const barWidth = Number.isFinite(width) && width > 0 ? Math.floor(width) : 10;
  const safePercent = Number.isFinite(percent) ? percent : 0;
  const filled = Math.max(0, Math.min(barWidth, Math.round((safePercent / 100) * barWidth)));
  const empty = barWidth - filled;
  return '■'.repeat(filled) + '□'.repeat(empty);
}

function isValidKey(info: KeyInfo): boolean {
  return !!(info.usage && info.usage.total && info.usage.total > 0);
}

function getPercent(info: KeyInfo): number {
  if (!isValidKey(info)) return 0;
  const used = info.usage!.used ?? 0;
  return Math.round((used / info.usage!.total!) * 100);
}

function formatUsage(info: KeyInfo): string {
  if (!isValidKey(info)) return '';
  if (info.usage?.display) return info.usage.display;
  if (info.usage?.mode === 'rate') return `${Math.round(info.usage.used ?? 0)}%`;
  const used = info.usage!.used ?? 0;
  const total = info.usage!.total!;
  const formatNum = (n: number) => n >= 1000000 ? `${Math.round(n / 1000000)}M` : `${Math.round(n / 1000)}K`;
  return `${formatNum(used)}/${formatNum(total)}`;
}

function buildContextMenu(keys: KeyInfo[]): Menu {
  const template: Electron.MenuItemConstructorOptions[] = [];
  const current = keys.find(k => k.isCurrent);
  const others = keys.filter(k => !k.isCurrent);
  
  // All keys in one line each
  for (const info of keys) {
    const valid = isValidKey(info);
    let label: string;
    if (valid) {
      const percent = getPercent(info);
      const usage = formatUsage(info);
      const mark = info.isCurrent ? '●' : '○';
      label = `${mark}  ${maskKey(info.key)}\t${getProgressBar(percent, 8)}  ${usage}`;
    } else {
      const mark = info.isCurrent ? '●' : '○';
      label = `${mark}  ${maskKey(info.key)}\t${getProgressBar(0, 8)}  Invalid`;
    }
    template.push({
      label,
      click: info.isCurrent ? () => {} : async () => {
        const result = await useKey(info.index);
        if (result.success) {
          const newKeys = await getKeyList();
          updateTrayMenu(newKeys);
          notifyWebContents();
        }
      },
    });
  }
  
  if (keys.length > 0) {
    template.push({ type: 'separator' });
  }
  
  // Actions
  template.push({
    label: 'Dashboard',
    click: () => createMainWindow(),
    accelerator: 'CmdOrCtrl+O',
  });
  
  template.push({
    label: 'Quit',
    click: () => app.quit(),
    accelerator: 'CmdOrCtrl+Q',
  });
  
  return Menu.buildFromTemplate(template);
}

export function initTray(keys: KeyInfo[]): Tray {
  if (tray) {
    tray.destroy();
  }
  
  tray = new Tray(getTrayIcon());
  tray.setToolTip('oroio - API Key Manager');
  
  updateTrayMenu(keys);
  
  tray.on('click', () => {
    if (tray) {
      tray.popUpContextMenu();
    }
  });
  
  tray.on('double-click', () => {
    createMainWindow();
  });
  
  return tray;
}

export function updateTrayMenu(keys: KeyInfo[]): void {
  if (!tray) return;
  
  const menu = buildContextMenu(keys);
  tray.setContextMenu(menu);
  
  // Update tooltip with current key info
  const current = keys.find(k => k.isCurrent);
  if (current && current.usage) {
    const percent = current.usage.total 
      ? Math.round(((current.usage.used ?? 0) / current.usage.total) * 100)
      : 0;
    tray.setToolTip(`oroio · ${percent}% used`);
  }
}

export function getTray(): Tray | null {
  return tray;
}
