# DentCheck Icons

## Файлы

| Файл | Размер | Назначение |
|------|--------|------------|
| `favicon.ico` | 16+32+48px | Браузер (вкладка) |
| `favicon-16x16.png` | 16px | Браузер |
| `favicon-32x32.png` | 32px | Браузер retina |
| `favicon-48x48.png` | 48px | Windows taskbar |
| `apple-touch-icon.png` | 180px | iOS/Safari (основной) |
| `apple-touch-icon-120x120.png` | 120px | iPhone |
| `apple-touch-icon-152x152.png` | 152px | iPad |
| `apple-touch-icon-167x167.png` | 167px | iPad Pro |
| `apple-touch-icon-180x180.png` | 180px | iPhone 6+ |
| `android-chrome-192x192.png` | 192px | Android Chrome |
| `android-chrome-512x512.png` | 512px | Android Chrome / PWA |
| `icon-1024x1024.png` | 1024px | App Store |
| `icon.png` | 1024px | Expo → app.config.ts `icon` |
| `adaptive-icon.png` | 1024px | Expo → app.config.ts `adaptiveIcon` |
| `site.webmanifest` | — | PWA manifest |

## Подключение в HTML

```html
<link rel="icon" type="image/x-icon" href="/icons/favicon.ico">
<link rel="icon" type="image/png" sizes="32x32" href="/icons/favicon-32x32.png">
<link rel="icon" type="image/png" sizes="16x16" href="/icons/favicon-16x16.png">
<link rel="apple-touch-icon" sizes="180x180" href="/icons/apple-touch-icon.png">
<link rel="manifest" href="/icons/site.webmanifest">
<meta name="theme-color" content="#111111">
```

## Expo (app.config.ts)

```ts
icon: './assets/images/icon.png',
android: {
  adaptiveIcon: {
    foregroundImage: './assets/images/adaptive-icon.png',
    backgroundColor: '#111111',
  },
},
```
