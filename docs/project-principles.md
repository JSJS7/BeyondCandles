# Zasady projektu StockEngine

## Cel projektu

StockEngine to edukacyjno-portfolio projekt publicznej aplikacji webowej do wyszukiwania i analizowania spółek giełdowych.

Główny cel edukacyjny to zrozumienie przepływu danych i architektury systemu:

frontend → backend → baza danych → kolejka zadań → workery → obliczenia → wyniki → frontend.

Projekt rozwijam przede wszystkim, aby nauczyć się backend engineeringu, baz danych, C++, wielowątkowości, systemów rozproszonych, infrastruktury i wdrażania aplikacji.

Aplikacja prezentuje dane oraz wyniki obliczeń technicznych. Nie stanowi porady inwestycyjnej.

## Cel edukacyjny i praca dyplomowa

Projekt ma tworzyć działającą aplikację oraz system analizy danych finansowych, ale jego równie ważnym celem jest jak najlepsza nauka inżynierii oprogramowania.

Każda nowa technologia, decyzja architektoniczna i istotny fragment kodu powinny być dokładnie wyjaśnione:

- jaki problem rozwiązują;
- dlaczego są potrzebne właśnie na tym etapie;
- jakie istnieją alternatywy;
- jakie są zalety, wady i konsekwencje wybranego rozwiązania;
- jak dane przepływają przez system.

Celem nie jest tylko działający kod, lecz świadome rozumienie implementacji i architektury. Nie należy dodawać „magicznych” fragmentów kodu ani technologii, których działania nie rozumiem.

Projekt ma być rozwijany jako potencjalna praca dyplomowa. Jej głównym tematem będzie projekt, implementacja oraz ocena wydajności rozproszonego systemu przetwarzania danych finansowych, a aplikacja webowa będzie jego interfejsem i demonstratorem.

Część badawcza będzie obejmować eksperymenty dotyczące wpływu liczby workerów, liczby wątków w workerach oraz granulacji zadań na:

- czas wykonania zadań i całego batcha;
- przepustowość systemu;
- czas oczekiwania zadań w kolejce;
- wykorzystanie CPU i pamięci RAM;
- zachowanie systemu dla różnych rozmiarów batcha;
- wpływ retry i awarii workerów na niezawodność.

Wyniki eksperymentów mają być mierzone i analizowane obiektywnie. Nie zakładamy z góry, że większa liczba wątków lub workerów zawsze poprawi wydajność.

## Zasada pracy

Pracujemy iteracyjnie:

1. Definiujemy jeden konkretny problem.
2. Wyjaśniamy, po co potrzebne jest rozwiązanie.
3. Poznaję minimalną potrzebną część technologii.
4. Implementuję mały fragment.
5. Sprawdzamy działanie i rozumiem wynik.
6. Robimy mały, opisowy commit.
7. Dopiero wtedy wybieramy następny krok.

Nie implementujemy wielu milestone’ów naraz i nie generujemy całego projektu z góry.

## Zasady dla AI

AI ma być nauczycielem i partnerem technicznym, a nie automatem generującym cały projekt.

AI powinno:

- prowadzić pracę krok po kroku;
- przed implementacją wyjaśniać problem i cel;
- wskazywać możliwe rozwiązania oraz ich kompromisy, jeśli wybór jest istotny;
- proponować małe, sprawdzalne zmiany;
- pomagać analizować błędy i wyjaśniać ich przyczynę;
- uruchamiać testy lub proponować jasny sposób weryfikacji;
- dbać o czytelność kodu, konfigurację przez zmienne środowiskowe i sensowne commity;
- zapisywać istotne decyzje architektoniczne jako ADR.

AI nie powinno:

- dodawać technologii tylko dlatego, że są popularne;
- wyprzedzać aktualnego milestone’u;
- budować całej aplikacji bez uzgodnienia;
- ukrywać złożoności za dużą ilością gotowego kodu;
- umieszczać sekretów, kluczy API ani plików `.env` w repozytorium;
- traktować wielowątkowości, kolejek albo systemów rozproszonych jako sztucznych dodatków.

## Podejście do architektury

Aplikacja od początku ma być projektowana tak, aby mogła później działać publicznie. Różnica między developmentem i production ma wynikać głównie z konfiguracji oraz środowiska uruchomieniowego, nie z przepisywania kodu.

Docelowy kierunek technologiczny:

- Angular + TypeScript jako frontend;
- Python + FastAPI jako backend i API;
- PostgreSQL jako trwała relacyjna baza danych;
- Redis jako cache i dane krótkotrwałe;
- RabbitMQ jako kolejka zadań;
- C++ jako silnik cięższych obliczeń;
- gRPC + Protocol Buffers do komunikacji Python ↔ C++;
- Docker do uruchamiania usług lokalnie;
- Prometheus i Grafana do obserwowalności.

Technologie wdrażamy dopiero, gdy pojawi się problem, który rozwiązują.

## Jakość i dokumentacja

- Konfiguracja zależna od środowiska używa zmiennych środowiskowych.
- Każda funkcja powinna mieć test automatyczny lub opisany scenariusz testu ręcznego.
- Ważne decyzje architektoniczne zapisujemy w `docs/decisions/`.
- README opisuje aktualny stan projektu oraz sposób uruchomienia.
- Commity są małe i opisowe, np. `chore: add project principles`.
- Przed optymalizacją najpierw mierzymy problem.

## Aktualny etap

Jesteśmy na M0 — Foundation.

Aktualnie przygotowujemy dokumentację, strukturę repozytorium i środowisko. Nie implementujemy jeszcze funkcji aplikacji, bazy danych, kolejek ani workerów.