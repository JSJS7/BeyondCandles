# BeyondCandles

Aplikacja edukacyjna do historycznej analizy spółek giełdowych.

## Lokalne dane historyczne

Projekt ma importer dziennych notowań 200 wybranych spółek (2015–2024)
oraz próbę integracji raportów finansowych 10 firm z SEC EDGAR.
Instrukcja przygotowania bazy, weryfikacji i odtworzenia danych:
[docs/data-import.md](docs/data-import.md).

## Uruchomienie aplikacji lokalnie

Przygotowanie PostgreSQL, `.env` i środowiska Pythona opisuje instrukcja danych powyżej.
Backend uruchom z katalogu `backend`:

```powershell
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload
```

Frontend uruchom w drugim terminalu, z katalogu `frontend`:

```powershell
npm ci
npm start
```

Frontend działa pod `http://localhost:4200`, a API pod `http://127.0.0.1:8000`.
Obecne endpointy spółek nadal zwracają listę demonstracyjną; podłączenie odczytu
z przygotowanej bazy jest następnym proponowanym etapem w [roadmapie](docs/roadmap.md).

## Katalog projektu

Docelowa ścieżka po zmianie nazw to `ProjektBeyondCandles/BeyondCandles`. Katalogi `frontend`,
`backend` i `engine` zachowują nazwy opisujące ich odpowiedzialności.

## Istniejące środowisko lokalne

Nazwa projektu Docker Compose `stockengine` oraz identyfikatory bazy i użytkownika
w `.env` oraz `.env.example` pozostają zgodne z istniejącym środowiskiem PostgreSQL.
Zmiana nazwy aplikacji nie wymaga zmiany bazy ani utworzenia nowego wolumenu danych.

Po zmianie katalogów otwórz projekt ponownie w edytorze i aplikacji Codex.
Przed aktywacją środowiska Pythona zamknij terminal używający starej ścieżki.

Jeśli Windows blokuje zmianę nazw katalogów, zamknij aplikacje korzystające
z projektu i uruchom `Rename-ToBeyondCandles.ps1` z katalogu nadrzędnego projektu.
Skrypt zmienia nazwy obu katalogów i aktualizuje ścieżki lokalnych środowisk Pythona.
