# BeyondCandles

Edukacyjna aplikacja do historycznej analizy spółek: wyszukiwarka 200 firm,
wykres dziennego Adjusted Close i roczne wyniki finansowe z SEC EDGAR.

Ceny obejmują **2015-01-02–2024-12-20**: 200 spółek i 502 000 notowań.
Aktywne ceny pochodzą z Benjaminpo v81, z opisanymi korektami AVGO, DHR i JCI oraz jednego notowania AMD.
To stały wybór ze snapshotu, nie historyczny skład S&P 500. Lista i źródło:
`data/companies-200.json`, `data/manifest.json`. Roczne finanse pokazują najnowsze
zaimportowane wersje raportów; nie są jeszcze danymi do backtestu informacji.

## Przygotowanie lokalnej bazy

Potrzebne są Python, Node.js i Docker z PostgreSQL. Z katalogu głównego:

Na istniejącej instalacji zachowaj dotychczasowy `.env`, użytkownika, hasło i wolumen.
Poniższe polecenie tworzy `.env` tylko wtedy, gdy plik jeszcze nie istnieje.

```powershell
if (-not (Test-Path -LiteralPath .env)) {
    Copy-Item -LiteralPath .env.example -Destination .env -ErrorAction Stop
}
```

Jeśli `.env` został właśnie utworzony, uzupełnij go przed uruchomieniem bazy.
Następnie:

```powershell
docker compose up -d postgres
cd backend
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Projekt Compose nadal nazywa się `stockengine`. Nie resetuj bazy.
PostgreSQL jest dostępny z hosta przez `127.0.0.1:5432`; opublikowany port
nie jest przypisany do adresów sieci lokalnej komputera.
W istniejącym `.env` ustaw `POSTGRES_HOST=127.0.0.1`, aby połączenie używało
bezpośrednio IPv4 zamiast najpierw próbować adresu IPv6 dla `localhost`.

Metadane katalogu są zapisane w wersjonowanym `data/company-metadata.csv`.
Pochodzenie i SHA-256 wyciągu z Andrew v1022 są w obu manifestach; starego ZIP-a
nie trzeba pobierać. Ceny pochodzą z 200 plików Benjaminpo v81.
Na innym komputerze przywróć też katalog `data/raw/benjaminpo-v81/evidence/`
z jego przypiętymi dowodami korekt; same CSV nie odtwarzają archiwum dowodów.
Z katalogu `backend`:

```powershell
.\.venv\Scripts\python.exe -m app.data_pipeline inspect-db
.\.venv\Scripts\python.exe -m app.data_pipeline fetch-prices
.\.venv\Scripts\python.exe -m app.data_pipeline validate-prices
.\.venv\Scripts\python.exe -m alembic upgrade head
.\.venv\Scripts\python.exe -m app.data_pipeline import-prices
.\.venv\Scripts\python.exe -m app.data_pipeline fetch-sec --scope all
.\.venv\Scripts\python.exe -m app.data_pipeline audit-sec --scope all
.\.venv\Scripts\python.exe -m app.data_pipeline import-sec --scope all
.\.venv\Scripts\python.exe -m app.data_pipeline verify-db
```

Pobieranie SEC wymaga `SEC_USER_AGENT` z nazwą aplikacji i rzeczywistym kontaktem
w lokalnym `.env`. Przypisania 200 emitentów są w `data/sec-identifiers.json`;
`resolve-sec` służy ich weryfikacji i ponownemu przygotowaniu. Pobieranie jest
sekwencyjne, zachowuje cache i postęp po błędzie. Import odbywa się transakcyjnie;
ponowne uruchomienie nie dodaje kopii ani nie nadpisuje sprzecznych danych.

Importer cen obsługuje jeden przypięty zbiór. `data/price-adjustments.json`
zawiera aktywne 39 dywidend AVGO, a manifest opisuje pozostałe korekty.
Zwykły `import-prices` odrzuca sprzeczne wartości; jawne zmiany istniejących
cen wykonuje `replace-prices` z kontrolą odcisku bazy i kopią bezpieczeństwa.

Kontrola jakości całego katalogu z lokalnym cache SEC:
`python -m app.data_pipeline audit-prices`. Wypisuje wynik jako JSON, bez tworzenia
osobnego raportu. Kod wyjścia 1 oznacza przypadki wymagające sprawdzenia, m.in.
braki późniejszych korekt dywidend i duże zmiany współczynników. Duży skok może
być prawidłową dywidendą specjalną albo wydzieleniem — audyt sam go nie usuwa.
Pozostałe serie zachowują wartości dostawcy i nie są jeszcze w pełni zweryfikowane
pod kątem zdarzeń korporacyjnych. Poprawna suma kontrolna i `verify-db` potwierdzają
powtarzalność importu, a nie pełną poprawność ekonomiczną cen.

Archiwa, cache, kopie bazy i raporty w `data/raw/` oraz `data/processed/` są
ignorowane przez Git. Rozszerzona dokumentacja w lokalnym `docs/` również jest
ignorowana zgodnie z przyjętym układem repozytorium.

## Aktywne źródło cen: Benjaminpo v81

Podmianę zakończono 22.10.2026: **200 spółek i 502 000 cen**, bez zmian katalogu,
identyfikatorów ani 182 535 faktów SEC. `data/benjaminpo-v81.json` przypina wersję,
adresy i SHA-256 wszystkich 200 oryginalnych CSV oraz dowody i reguły korekt;
`data/manifest.json` jest aktywną kopią tego opisu. Oba muszą być zgodne.

- **AVGO:** 39 zweryfikowanych dywidend, bez podwójnych wypłat z 2018 roku.
- **DHR/Fortive:** zachowana konwersja cen 1319:1000 dostawcy i usunięty dodatkowy
  czynnik 24,56 USD. Zmiana Adjusted Close 05.07.2016 wynosi około +3,64%, zamiast +61,22%.
  Wynik za 2016 mieści się w przedziale wynikającym z zaokrąglonego wykresu emitenta.
- **JCI/Adient:** jawna konwencja wyceny wydzielonych akcji po pierwszym zamknięciu:
  45,51 USD z raportu Adient × 1/10 = 4,551 USD na JCI, zamiast niezweryfikowanego
  szacunku 4,70 USD. Wsteczny czynnik wynosi `(poprzedni Close − wycena) / poprzedni Close`.
  Zmiana Adjusted Close 31.10.2016 wynosi około +2,83%.

To konwencje historycznego indeksu cenowego. Wydzielone akcje nie są zwykłą
wypłatą gotówki, a wykres nie rekonstruuje dokładnie portfela trzymającego oba walory.
OHLC, wolumen i końcowa kotwica Adjusted Close zachowują podstawę nowego dostawcy,
w tym zdarzenia po 2024. Przed łączeniem z EPS trzeba uzgodnić podstawę liczby akcji.
Te trzy przeglądy nie certyfikują wszystkich zdarzeń w całym katalogu.

**AMD 02.01.2015:** brakujące notowanie zastępuje jawna korekta z przypiętej
rewizji DoltHub: OHLC `2.70 / 2.73 / 2.64 / 2.69 USD`, wolumen `8330255`.
Close `2.69 USD` dodatkowo potwierdza dokument Scotiabank; pozostałe pola
pochodzą z DoltHub. Adjusted Close wynosi `2.69 USD`, na tej samej podstawie
co zamrożona historia AMD. Oryginalny CSV jest zachowany; przygotowanie danych
odtwarza poprawkę z obu manifestów. Dowody i deklaracja CC BY-SA 4.0 DoltHub
są przypięte w `reviews.AMD`. Szczegóły w `docs/data-import.md`.

Z katalogu `backend`:

```powershell
.\.venv\Scripts\python.exe -m app.data_pipeline fetch-prices
.\.venv\Scripts\python.exe -m app.data_pipeline prepare-prices
.\.venv\Scripts\python.exe -m app.data_pipeline verify-db
```

Pobieranie dotyczy wyłącznie 200 CSV, korzysta z cache i wznawia przerwaną partię.
Dowody w `data/raw/benjaminpo-v81/evidence/` zachowujemy jako część zamrożonego
wejścia; bieżącej odpowiedzi Yahoo nie podstawiamy automatycznie za zarchiwizowaną.
Przygotowany plik i wyniki kontroli są w `data/processed/benjaminpo-v81/`.

Kopie wcześniejszych cen pozostają lokalnie w `data/raw/backups/`. Obok każdej
kopii CSV znajduje się JSON z SHA-256, manifestem i przypisaniami ID. Przywracanie wymaga
`restore-prices --backup <pełna-ścieżka-do-kopii.csv> --expected-price-fingerprint <odcisk-obecnych-cen>`.
Odcisk aktywnych cen po korekcie AMD: `dfb74c3b9774537c20d71c33442a810f94846a4f7383f5dce401444adf8ef389`.
Zwykły import oraz ponowienie podmiany nie zmieniają danych po udanej wymianie.

Wydawca Kaggle deklaruje GPL 3. Prawa do publicznego wyświetlania danych pochodzących
z Yahoo nie zostały niezależnie potwierdzone; lokalna podmiana nie zatwierdza publikacji.
Archiwa, cache, dowody i kopie pozostają poza Gitem. Nie dodano nowych zależności.

## Equity z SEC

Widok wybiera kapitał właścicieli jednostki dominującej na koniec okresu,
łącznie z kapitałem uprzywilejowanym zaliczonym do equity i bez udziałów
niekontrolujących. Reguły w `data/equity-rules.json` zatwierdzają wszystkie 2001
okresów obecnego snapshotu 200 spółek; Equity nie ma pozostałych `Needs review`.
Sam pojedynczy tag nie daje już automatycznie statusu `Ready`.

Z katalogu `backend`:

```powershell
.\.venv\Scripts\python.exe -m app.data_pipeline review-equity --fetch
.\.venv\Scripts\python.exe -m app.data_pipeline review-equity
```

Pierwsze polecenie uzupełnia cache tabel, not i potrzebnych oryginalnych plików
XBRL SEC; drugie odtwarza kontrolę offline.
Kod zakończenia 1 ze statusem `completed_with_gaps` oznacza pozostające
nierozstrzygnięte okresy. Przegląd nie zmienia PostgreSQL ani pozostałych metryk.
Pozycje pomijane przez Companyfacts są zapisane z oryginalnym znacznikiem,
kolumną, datą i adresem raportu. Oryginalny XBRL rozróżnia dokładny dzień,
właścicieli, składniki kapitału i korekty standardów. Szczególne pozycje oraz
kolumny udziałów niekontrolujących mają przypięte dowody
w `data/equity-exceptions.json`. Zmiana snapshotu wymaga ponownej weryfikacji.

## Operating cash flow z SEC

Widok preferuje operacyjne przepływy całej spółki, również działalności
zaniechanej. Gdy pełna kwota nie jest dostępna, pokazuje zatwierdzoną wartość
działalności kontynuowanej z oznaczeniem `Continuing operations`.
Dla obecnego snapshotu: **2001 gotowych wartości — 1981 total i 20 continuing**,
w tym 59 obliczeń. OCF nie ma `Not available` ani `Needs review`.
Pojedynczy tag nie jest automatycznie uznawany za poprawny total.

Z katalogu `backend`:

```powershell
.\.venv\Scripts\python.exe -m app.data_pipeline review-operating-cash-flow --fetch
.\.venv\Scripts\python.exe -m app.data_pipeline review-operating-cash-flow
```

Pierwsze polecenie uzupełnia źródłowe zestawienia SEC i przypięte dowody
z wykorzystaniem cache; drugie odtwarza przegląd offline. Reguły wyboru w
`data/operating-cash-flow-rules.json` są używane przez API. Dokładne źródła
24 zakończonych wyjątków są w `data/operating-cash-flow-exceptions.json`.
Indeks dowodów w `data/raw/sec/operating-cash-flow-evidence-index.json`
i raport `data/processed/sec-operating-cash-flow-report.json` są poza Gitem.
`reviewed` i kod 0 oznaczają zakończenie przeglądu, również z rozpoznanymi
brakami danych. `completed_with_gaps` i kod 1 oznaczają nierozstrzygnięte
przypadki. Przegląd nie modyfikuje PostgreSQL ani pozostałych metryk.

Gotowy total ma pierwszeństwo; w przeciwnym razie dodawane są osobno wykazane
operacyjne przepływy działalności kontynuowanej i zaniechanej, z tego samego
raportu i okresu. Zachowujemy znaki, źródła i oznaczenie `Calculated`.
Nie dodajemy przepływów inwestycyjnych lub finansowych ani zysku działalności
zaniechanej. Etykieta i sekcja raportu rozstrzygają zakres także wtedy, gdy
emitent niewłaściwie używa standardowego znacznika.

RTX FY2017/FY2022, FI FY2019 i MSI FY2015 mają jawnie raportowane zero
operacyjnej części zaniechanej we wcześniejszym kompletnym zestawieniu.
Kwota kontynuowana jest identyczna z nowszą. Zachowujemy obie części
wcześniejszego raportu i jego rzeczywistą datę złożenia, bez mieszania wersji;
nowsza pozycja pozostaje wariantem i jawną kontrolą zgodności.

Dla C i GD FY2015–FY2024 wybieramy raportowany OCF działalności kontynuowanej.
Pełnej kwoty nadal nie ustalono: C opisuje przepływy zaniechane jako nieistotne
bez kwoty, GD podaje ich sumę bez podziału operacyjne/inwestycyjne/finansowe.
API zachowuje `scope: "continuing"`; dla pozostałych OCF zwraca `scope: "total"`.
Tabela oznacza węższy zakres i wyjaśnia go w Sources. Wartość jest dostępna
do analiz, ale obliczenia muszą uwzględniać jej zakres. Nie traktujemy jej
automatycznie jako totalu. Zmiana snapshotu wymaga ponownej weryfikacji.

## EPS z SEC

Jedna kolumna **EPS** ma **2001 gotowych wartości: 1991 diluted i 10 basic**,
bez Not available ani Needs review w dostępnych okresach. Preferujemy rozwodniony
wynik netto. Dla BRK-B FY2015–FY2024 pokazujemy zweryfikowany basic klasy B
z etykietą `Basic`; nie oznacza to potwierdzenia równoważności z diluted.
API zachowuje `eps_basis: "basic" | "diluted"` przy tej samej wartości `value`.
Historyczny klucz API `eps_diluted` pozostaje zgodny z dotychczasowym klientem,
ale o rodzaju wybranego EPS decydują metadane, a nie nazwa tego klucza.

Końcowy audyt poprawił AXON FY2021: EPS `−0,91 → −0,85 USD/akcję`, zgodnie z
[notą rewizji SEC](https://www.sec.gov/Archives/edgar/data/1069183/000106918324000061/R26.htm).
Dodano jeden oryginalny fakt XBRL; wcześniejsze wersje pozostają w bazie.

Wyjątki mają dokładne reguły w `data/eps-rules.json`, zamiast globalnego uznawania
basic lub continuing za zamienniki diluted. Klasy akcji Visa, Alphabet, KKR
i Berkshire są rozróżniane. Zmiana nie przelicza ani nie nadpisuje źródłowych kwot.
API przelicza EPS na wspólną podstawę liczby akcji z **13.10.2026**, zgodną z datą
końcową oryginalnych plików cen v81. Zmienia to 203 wartości w 34 firmach;
pozostałe 1798 zachowuje wysokość. Oryginalne fakty SEC pozostają w bazie bez zmian.
`eps_share_basis` zachowuje datę, raportowaną wartość, dokładny współczynnik oraz
zastosowane splity ze źródłami. Już uwzględnionych splitów nie naliczamy drugi raz.
Reguły 55 zdarzeń i zatwierdzenia źródeł dla 200 firm zawiera
`data/eps-split-rules.json`; po zmianie źródeł normalizacja wymaga nowej kontroli.
`review-eps-splits` sprawdza lokalne oryginały i odtwarza kontrolę bez zapisu bazy.

Normalizacja splitów nie usuwa korekt dywidendowych i wydzieleń z Adjusted Close.
Nie należy obliczać P/E przez podzielenie tego pola przez EPS. Zdarzenia zmieniające
zakres biznesu nadal wymagają uwzględnienia przy analizie porównywalności wyników.
Widok wykorzystuje najnowsze wersje raportów, również późniejsze niż analizowany rok;
nie nadaje się jeszcze do backtestu według informacji dostępnych danego dnia.
Przegląd odtwarza się poleceniem `review-eps` (z `--fetch` pobiera brakujące dowody);
import używa istniejącego `import-sec --scope all`. Szczegóły w lokalnej instrukcji
`docs/data-import.md`.

## Uruchomienie i testy

Backend, z `backend`:

```powershell
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload
```

Frontend, w drugim terminalu z `frontend`:

```powershell
npm.cmd ci
npm.cmd start
```

Strona: `http://localhost:4200`. API i dokumentacja:
`http://127.0.0.1:8000/docs`. Odczyt aplikacji korzysta z PostgreSQL,
bez połączeń przeglądarki z SEC. Braki to `Not available`, a różne definicje
i konflikty to `Needs review`; szczegóły źródła można rozwinąć w tabeli.
Odczyty API mają limit oczekiwania 5 sekund. Po błędzie można ponowić pobranie
przyciskiem `Try again`; na stronie spółki każda sekcja jest ponawiana oddzielnie.

Testy backendu wymagają `pip install -r requirements-dev.txt`, następnie
`python -m unittest discover -s tests -v`. Ustawienie `BC_RUN_DB_TESTS=1`
włącza migracje i importy na osobnej, losowo nazwanej bazie testowej.
Frontend: `npm.cmd test -- --watch=false` i `npm.cmd run build`.

## Narzędzia do pracy z kodem

Konfiguracja narzędzi zakłada Python 3.13, zgodnie z używanym backendowym `.venv`.
Zainstaluj narzędzia developerskie w tym środowisku:
`python -m pip install -r requirements-dev.txt` (z katalogu `backend`).
Ruff jest przypięty do wersji 0.17.2; wspólne reguły formatowania, importów
oraz podstawowej kontroli typów są w `backend/pyproject.toml`.

Z katalogu `backend`:

```powershell
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m ruff format --check .
```

Aby uporządkować importy i sformatować zmienione pliki, użyj
`python -m ruff check --select I --fix <pliki>` oraz
`python -m ruff format <pliki>`. Używaj interpretera z backendowego `.venv`.
Kontrolę typów Pythona na poziomie `basic` można odtworzyć poleceniem
`uvx --from pyright==1.1.414 pyright` z katalogu `backend`.

Z katalogu `frontend`:

```powershell
npm.cmd run format:check
npm.cmd run typecheck
npm.cmd test -- --watch=false
npm.cmd run build
```

`npm.cmd run format` formatuje pliki TS, HTML i CSS przez lokalnego Prettiera.
`typecheck` sprawdza kod aplikacji, szablony Angulara i typy testów; włączone
są `strict` i `strictTemplates`.

W VS Code używamy rozszerzeń Python, Pylance, Ruff, Prettier oraz Angular
Language Service. Lokalne ustawienia `.vscode/settings.json` wybierają
backendowe `.venv`, kontrolę typów `basic`, Ruff dla Pythona i Prettiera dla
TS/HTML/CSS. Formatowanie działa przy zapisie; automatyczny zapis odbywa się
po zmianie fokusu. Lokalne `.vscode/extensions.json` zawiera rekomendacje
rozszerzeń. Katalog `.vscode/` pozostaje poza Gitem zgodnie z dotychczasową
konfiguracją; na innym komputerze trzeba odtworzyć te ustawienia edytora.
