package handlers

import (
	"encoding/json"
	"net/http"
	"net/url"
	"strings"
	"urlshortener/internal/storage"
)

// Handler объединяет зависимости HTTP-обработчиков.
type Handler struct {
	store storage.Storage
}

// New создает набор обработчиков.
func New(store storage.Storage) *Handler {
	return &Handler{store: store}
}

type shortenRequest struct {
	URL string `json:"url"`
}

type shortenResponse struct {
	Code      string `json:"code"`
	ShortURL  string `json:"short_url"`
	Original  string `json:"original_url"`
}

// Shorten обрабатывает POST /shorten.
func (h *Handler) Shorten(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}

	var req shortenRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		http.Error(w, "invalid json body", http.StatusBadRequest)
		return
	}

	if !isValidURL(req.URL) {
		http.Error(w, "invalid url", http.StatusBadRequest)
		return
	}

	created, err := h.store.Create(req.URL)
	if err != nil {
		http.Error(w, "failed to shorten url", http.StatusInternalServerError)
		return
	}

	shortURL := "http://" + r.Host + "/" + created.Code
	resp := shortenResponse{
		Code:     created.Code,
		ShortURL: shortURL,
		Original: created.Original,
	}

	writeJSON(w, http.StatusCreated, resp)
}

// Redirect обрабатывает GET /{code}.
func (h *Handler) Redirect(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}

	code := strings.TrimPrefix(r.URL.Path, "/")
	if code == "" || strings.Contains(code, "/") {
		http.NotFound(w, r)
		return
	}

	item, err := h.store.GetByCode(code)
	if err != nil {
		if err == storage.ErrNotFound {
			http.NotFound(w, r)
			return
		}
		http.Error(w, "internal server error", http.StatusInternalServerError)
		return
	}

	http.Redirect(w, r, item.Original, http.StatusFound)
}

// Stats обрабатывает GET /stats/{code}.
func (h *Handler) Stats(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}

	const prefix = "/stats/"
	if !strings.HasPrefix(r.URL.Path, prefix) {
		http.NotFound(w, r)
		return
	}

	code := strings.TrimPrefix(r.URL.Path, prefix)
	if code == "" || strings.Contains(code, "/") {
		http.NotFound(w, r)
		return
	}

	stats, err := h.store.GetStats(code)
	if err != nil {
		if err == storage.ErrNotFound {
			http.NotFound(w, r)
			return
		}
		http.Error(w, "internal server error", http.StatusInternalServerError)
		return
	}

	writeJSON(w, http.StatusOK, stats)
}

// writeJSON единообразно пишет JSON-ответ.
func writeJSON(w http.ResponseWriter, status int, payload any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(payload)
}

// isValidURL проверяет базовую корректность URL.
func isValidURL(raw string) bool {
	u, err := url.ParseRequestURI(raw)
	if err != nil {
		return false
	}
	return u.Scheme == "http" || u.Scheme == "https"
}
