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
- Dodano stronę szczegółów spółki pod adresem `/stocks/:ticker`.
- Dodano endpoint `GET /stocks/{ticker}` oraz obsługę odpowiedzi `404` dla nieistniejącej spółki.
- Dodano ogólną stronę `Page not found` dla nieistniejących tras.
- Zabezpieczono pobieranie szczegółów przed wyścigiem odpowiedzi przy szybkiej zmianie tickera przez `switchMap`.
- Ujednolicono angielski język komunikatów interfejsu.

## Do dokończenia w M1

- Dodać testy backendu dla `GET /stocks/{ticker}` oraz testy frontendu dla `StockService`, widoków błędów i tras.
- Sprawdzić ręcznie scenariusze: istniejąca spółka, nieistniejąca spółka, nieistniejąca trasa oraz szybka zmiana spółki.

## Świadomie odłożone na później

- Lista `STOCKS` w pamięci i liniowe wyszukiwanie jednej spółki są tymczasowe; PostgreSQL oraz indeks tickera zastąpią je w późniejszym etapie.
- Topbar pobiera obecnie całą małą listę spółek. Docelowo wyszukiwanie będzie wykonywane po stronie backendu, z limitem wyników i opóźnieniem wpisywania.
- Adres backendu jest na razie wpisany w `ApiService`; konfiguracja zależna od środowiska zostanie dodana przed rozdzieleniem development i production.
- Sidebar jest obecnie wizualnym szkieletem menu. `routerLink` i aktywne pozycje dodamy wraz z tworzeniem rzeczywistych stron funkcjonalnych.
- Widok szczegółów pokazuje obecnie jeden ogólny układ dla błędów HTTP. Dokładniejsze rozróżnienie komunikatów dla `404`, błędów sieciowych i błędów serwera dodamy później.

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
