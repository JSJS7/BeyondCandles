# Roadmap projektu StockEngine

## Aktualny milestone

M1 — First Frontend–Backend Vertical Slice

Celem etapu jest zbudowanie i zrozumienie pierwszego pełnego przepływu danych:

frontend → API → backend → odpowiedź JSON → frontend.

## Zakończone

- Utworzono repozytorium GitHub `StockEngine`.
- Utworzono podstawową strukturę `frontend/`, `backend/`, `engine/` i `docs/`.
- Skonfigurowano Git i `.gitignore`.
- Zainstalowano Docker Desktop oraz zweryfikowano jego działanie.
- Utworzono aplikację Angular.
- Utworzono backend FastAPI.
- Dodano endpoint `GET /health`.
- Dodano endpoint `GET /stocks`.
- Dodano CORS dla lokalnego frontendu.
- Połączono Angular z FastAPI przez `HttpClient`.
- Dodano `ApiService`, `HealthService` i `StockService`.
- Wyszukiwarka pobiera tymczasową listę spółek z backendu.

## Aktualny cel

Dopracować pierwszy przepływ danych w interfejsie:

- pokazać stan ładowania listy spółek;
- pokazać użytkownikowi błąd pobierania danych;
- dodać testy dla serwisów i komunikacji HTTP.

## Poza zakresem obecnego kroku

Nie dodajemy jeszcze:

- PostgreSQL;
- zewnętrznego API giełdowego;
- Redis;
- RabbitMQ;
- workerów;
- C++ engine;
- logowania użytkowników;
- Dockera dla całej aplikacji.