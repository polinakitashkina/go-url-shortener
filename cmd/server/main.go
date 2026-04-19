package main

import (
	"encoding/json"
	"log"
	"net/http"
	"os"
	"strings"

	"url-shortener/internal/handlers"
	"url-shortener/internal/middleware"
	"url-shortener/internal/storage"
)

func main() {
	port := os.Getenv("PORT")
	if port == "" {
		port = "8080"
	}

	// Инициализация хранилища
	store := storage.NewMemoryStorage()

	// Создание обработчиков
	urlHandler := handlers.NewURLHandler(store)

	// Настройка маршрутов
	mux := http.NewServeMux()

	// API эндпоинты
	mux.HandleFunc("POST /shorten", urlHandler.Shorten)
	mux.HandleFunc("GET /{code}", urlHandler.Redirect)
	mux.HandleFunc("GET /stats/{code}", urlHandler.GetStats)

	// HTML интерфейс
	mux.HandleFunc("GET /", func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/" {
			http.NotFound(w, r)
			return
		}
		w.Header().Set("Content-Type", "text/html; charset=utf-8")
		w.Write([]byte(indexHTML))
	})

	// Применяем middleware
	handler := middleware.Logging(mux)
	handler = middleware.Recover(handler)
	handler = middleware.RateLimit(handler)

	log.Printf("🚀 Сервер запущен на порту %s", port)
	log.Printf("📊 Интерфейс: http://localhost:%s", port)
	log.Printf("🔗 API: POST /shorten, GET /{code}, GET /stats/{code}")

	log.Fatal(http.ListenAndServe(":"+port, handler))
}

// indexHTML - полный HTML интерфейс (я сократил для экономии места,
// но вы можете попросить меня показать полную версию)
const indexHTML = `<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>URL Shortener</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
            background: #FFFFFF;
            color: #111111;
            padding: 2rem;
            transition: background 0.2s, color 0.2s;
        }
        body.dark {
            background: #0F0F12;
            color: #EDEDED;
        }
        .container { max-width: 800px; margin: 0 auto; }
        .header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 2rem;
        }
        h1 { font-size: 1.8rem; font-weight: 600; }
        .theme-toggle {
            background: #F5F5F5;
            border: none;
            padding: 0.5rem 1rem;
            border-radius: 8px;
            cursor: pointer;
            font-size: 1rem;
            transition: background 0.15s;
        }
        body.dark .theme-toggle {
            background: #1A1A1F;
            color: #EDEDED;
        }
        .card {
            background: #F5F5F5;
            border-radius: 12px;
            padding: 1.5rem;
            margin-bottom: 1rem;
            transition: background 0.2s;
        }
        body.dark .card {
            background: #1A1A1F;
        }
        input {
            width: 100%;
            padding: 0.8rem;
            border: 1px solid #E0E0E0;
            border-radius: 8px;
            font-size: 1rem;
            margin-bottom: 1rem;
            background: white;
        }
        body.dark input {
            background: #0F0F12;
            border-color: #2A2A2F;
            color: #EDEDED;
        }
        button {
            background: #2563EB;
            color: white;
            border: none;
            padding: 0.8rem 1.5rem;
            border-radius: 8px;
            font-size: 1rem;
            cursor: pointer;
            transition: background 0.15s;
        }
        button:hover { background: #1D4ED8; }
        .result {
            margin-top: 1rem;
            padding: 1rem;
            background: white;
            border-radius: 8px;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }
        body.dark .result {
            background: #0F0F12;
        }
        .copy-btn {
            background: #10B981;
            padding: 0.4rem 1rem;
            font-size: 0.9rem;
        }
        .copy-btn:hover { background: #059669; }
        .link-item {
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding: 1rem;
            border-bottom: 1px solid #E0E0E0;
        }
        body.dark .link-item {
            border-bottom-color: #2A2A2F;
        }
        .link-info { flex: 1; }
        .short-code {
            font-weight: 600;
            font-family: monospace;
            color: #2563EB;
            cursor: pointer;
        }
        .original-url {
            font-size: 0.85rem;
            color: #666;
            margin-top: 0.25rem;
        }
        body.dark .original-url { color: #888; }
        .clicks {
            font-size: 0.9rem;
            color: #666;
            margin-right: 1rem;
        }
        .arrow {
            font-size: 1.2rem;
            cursor: pointer;
            text-decoration: none;
            color: #2563EB;
        }
        .toast {
            position: fixed;
            bottom: 2rem;
            right: 2rem;
            background: #10B981;
            color: white;
            padding: 0.5rem 1rem;
            border-radius: 8px;
            opacity: 0;
            transition: opacity 0.2s;
        }
        @media (max-width: 600px) {
            body { padding: 1rem; }
            .link-item { flex-direction: column; align-items: flex-start; gap: 0.5rem; }
        }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>short.one</h1>
            <button class="theme-toggle" onclick="toggleTheme()">🌙 Dark</button>
        </div>
        <div class="card">
            <input type="text" id="urlInput" placeholder="https://very-long-link.com/page?ref=123..." />
            <button onclick="shortenUrl()">Сократить →</button>
            <div id="result" style="display: none;" class="result">
                <span id="shortUrl"></span>
                <button class="copy-btn" onclick="copyToClipboard()">Копировать</button>
            </div>
        </div>
        <div class="card">
            <h3 style="margin-bottom: 1rem;">Recent links</h3>
            <div id="linksList"></div>
        </div>
    </div>
    <div id="toast" class="toast">Copied!</div>

    <script>
        let currentShortUrl = '';
        
        async function shortenUrl() {
            const url = document.getElementById('urlInput').value;
            if (!url) return;
            
            const response = await fetch('/shorten', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ url: url })
            });
            
            if (response.ok) {
                const data = await response.json();
                currentShortUrl = data.short_url;
                document.getElementById('shortUrl').innerHTML = `<a href="${data.short_url}" target="_blank">${data.short_url}</a>`;
                document.getElementById('result').style.display = 'flex';
                loadLinks();
            }
        }
        
        async function loadLinks() {
            // В реальном проекте здесь будет запрос к API для получения списка ссылок
            // Пока показываем демо-данные
            const demoLinks = [
                { short_code: "7f3a9b", original_url: "https://google.com/long/search?query=url", clicks: 1337 },
                { short_code: "k9d2j", original_url: "https://youtube.com/watch?v=longvideo", clicks: 42 },
                { short_code: "x1p8q", original_url: "https://github.com/repo/issues/verylong", clicks: 891 }
            ];
            
            const container = document.getElementById('linksList');
            container.innerHTML = demoLinks.map(link => `
                <div class="link-item">
                    <div class="link-info">
                        <div class="short-code" onclick="copyCode('${link.short_code}')">${link.short_code}</div>
                        <div class="original-url">${link.original_url.substring(0, 50)}...</div>
                    </div>
                    <div style="display: flex; align-items: center; gap: 1rem;">
                        <span class="clicks">${link.clicks} clicks</span>
                        <a href="${link.original_url}" target="_blank" class="arrow">→</a>
                    </div>
                </div>
            `).join('');
        }
        
        function copyToClipboard() {
            navigator.clipboard.writeText(currentShortUrl);
            showToast();
        }
        
        function copyCode(code) {
            navigator.clipboard.writeText(window.location.origin + '/' + code);
            showToast();
        }
        
        function showToast() {
            const toast = document.getElementById('toast');
            toast.style.opacity = '1';
            setTimeout(() => { toast.style.opacity = '0'; }, 1500);
        }
        
        function toggleTheme() {
            document.body.classList.toggle('dark');
            localStorage.setItem('theme', document.body.classList.contains('dark') ? 'dark' : 'light');
        }
        
        const savedTheme = localStorage.getItem('theme');
        if (savedTheme === 'dark') document.body.classList.add('dark');
        
        loadLinks();
    </script>
</body>
</html>`
