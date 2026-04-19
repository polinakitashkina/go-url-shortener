package main

import (
	"log"
	"net/http"
	"os"
	"urlshortener/internal/handlers"
	"urlshortener/internal/middleware"
	"urlshortener/internal/storage"
)

func main() {
	store := storage.NewMemoryStorage()
	h := handlers.New(store)

	// Единая точка маршрутизации без сторонних роутеров.
	router := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch {
		case r.URL.Path == "/" && r.Method == http.MethodGet:
			http.ServeFile(w, r, "index.html")
		case r.URL.Path == "/shorten":
			h.Shorten(w, r)
		case len(r.URL.Path) > len("/stats/") && r.URL.Path[:len("/stats/")] == "/stats/":
			h.Stats(w, r)
		default:
			h.Redirect(w, r)
		}
	})

	// Подключаем middleware: сначала rate limit, затем логирование.
	limiter := middleware.NewRateLimiter(10)
	var app http.Handler = router
	app = limiter.Middleware(app)
	app = middleware.Logging(app)

	port := os.Getenv("PORT")
	if port == "" {
		port = "8080"
	}
	addr := ":" + port
	log.Printf("server started on %s", addr)
	if err := http.ListenAndServe(addr, app); err != nil {
		log.Fatalf("server failed: %v", err)
	}
}
