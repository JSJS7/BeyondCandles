# Instrukcje dla AI pracującego nad BeyondCandles

Ten plik określa sposób pracy AI, komunikacji z użytkownikiem i wprowadzania zmian.
Założenia samego projektu znajdują się w `docs/project-principles.md`, a aktualny stan i kolejne kroki w `docs/roadmap.md`.

## 1. Komunikacja i nauka

- Rozmawiaj po polsku. Kod, identyfikatory i komentarze zapisuj po angielsku. Dokumentacja może pozostać po polsku.
- Przed zmianą krótko wyjaśnij aktualny problem, cel zmiany i sposób jej sprawdzenia.
- Domyślnie pokazuj konkretny kod, plik i miejsce zmiany. Nie zadawaj ćwiczeń ani nie wymagaj samodzielnego znalezienia rozwiązania przed pokazaniem kodu.
- Wyjaśniaj nowe mechanizmy, składnię i istotne decyzje na przykładach z projektu. Omawiaj jedno zagadnienie naraz i pogłębiaj szczegóły, gdy użytkownik o nie pyta.
- Nie powtarzaj całej wcześniejszej lekcji, jeśli pytanie dotyczy konkretnego fragmentu.
- Przy istotnych decyzjach przedstaw alternatywy, kompromisy i rekomendację.
- Rozróżniaj potwierdzone fakty, założenia i propozycje. Nie przedstawiaj planowanych funkcji jako zaimplementowanych.
- Jeśli nie możesz odczytać projektu, powiedz to wprost. Jeśli wcześniejsza porada była błędna, wyjaśnij konkretnie przyczynę i korektę.

## 2. Kontekst i zakres pracy

- Przed rozpoczęciem pracy przeczytaj `docs/project-principles.md`. Przed oceną lub zmianą sprawdź aktualne pliki oraz dokumentację dotyczącą zadania. Aktualny etap sprawdzaj w `docs/roadmap.md`.
- Wcześniejsza rozmowa nie jest dowodem aktualnego stanu kodu. Nie twierdź, że sprawdziłeś pliki, których nie odczytałeś.
- Domyślnie realizuj jeden mały, spójny etap naraz. Większy zakres wykonuj, gdy użytkownik wyraźnie go zleci. Nie przechodź samodzielnie do kolejnych etapów ani nie generuj funkcji z wyprzedzeniem.
- Domyślnie podawaj kod do wklejenia. Edytuj pliki, gdy użytkownik wyraźnie o to poprosi. Pytanie o ocenę lub wyjaśnienie nie oznacza zgody na edycję.
- Zachowuj istniejące zmiany użytkownika. Nie poprawiaj niepowiązanych elementów bez uzgodnienia.
- Nie dodawaj technologii, zależności, płatnych usług ani kolejnych etapów bez rzeczywistej potrzeby i uzgodnienia.
- Przed dodaniem dokumentu lub raportu określ jego konkretne zastosowanie. Nie twórz osobnych plików wyłącznie po to, aby powielić podsumowanie rozmowy.

## 3. Pisanie i zmienianie kodu

- Wybieraj najprostsze rozwiązanie spełniające znane wymagania. Nie twórz dodatkowych warstw i abstrakcji wyłącznie dla hipotetycznych przyszłych funkcji.
- Uwzględniaj znane i rozsądnie przewidywalne przypadki brzegowe, błędy i wymagania bezpieczeństwa. Dobieraj najprostsze skuteczne zabezpieczenie; jeśli wymaga dodatkowej warstwy, wyjaśnij jej odpowiedzialność i potrzebę.
- Korzystaj z istniejących konwencji projektu. Ich zmianę uzasadniaj konkretnym problemem.
- Stawiaj czytelność dla człowieka ponad zwięzłość. Kod może być dłuższy, jeśli dzięki temu łatwiej zrozumieć jego działanie. Używaj opisowych nazw, zmiennych pośrednich i jawnych kroków; rozwijaj złożone jednolinijkowe wyrażenia, skrótowe warunki i lambdy, gdy utrudniają czytanie. Nie wydłużaj kodu bez poprawy zrozumiałości.
- W testach dodawaj krótki opis po angielsku, co jest sprawdzane i jaki wynik jest oczekiwany. Przy mniej oczywistych przypadkach wyjaśnij powód testu. Utrzymuj czytelny podział na przygotowanie danych, wykonanie i sprawdzenie wyniku; komentarze powinny wyjaśniać intencję, zamiast powtarzać oczywisty kod.
- Rozdzielaj odpowiedzialności UI, komunikacji z API, dostępu do danych i obliczeń, gdy pomaga to czytelności i testowaniu.
- Wydzielaj wspólne komponenty przy rzeczywistej powtarzalności lub wspólnej odpowiedzialności, nie tylko dla zwiększenia liczby plików.
- Przy rozwiązaniu tymczasowym wyjaśnij ograniczenie i warunek powrotu do tematu. Istotne odłożone problemy zapisuj w roadmapie.
- Optymalizację uzasadniaj pomiarem lub konkretnym ograniczeniem.
- Przy zmianie kontraktu API sprawdź korzystający z niego frontend. Przed zastosowaniem migracji wyjaśnij jej zawartość.
- Przy dodawaniu wskaźnika lub symulacji wyjaśnij definicję, założenia, wejście, wynik i ograniczenia.
- Przed integracją źródła danych sprawdź dostępność historii, limity i warunki przechowywania oraz publicznego wyświetlania danych.
- Przed operacją mogącą usunąć dane lub zmienić publiczne środowisko wyjaśnij zakres i uzyskaj zgodę, jeśli nie została już udzielona.

## 4. Weryfikacja, dokumentacja i Git

- Po zmianie uruchom odpowiednie testy albo wykonaj lub opisz ręczny sposób sprawdzenia. Podaj wynik i napisz, czego nie sprawdzono.
- Obliczenia sprawdzaj na przykładach ze znanym wynikiem, zanim zaczniesz optymalizację.
- Aktualizuj dokumentację objętą zmianą, jeśli zmieniają się opisane zachowanie, uruchamianie lub uzgodnione decyzje.
- Zachowuj podział odpowiedzialności: `AGENTS.md` opisuje sposób pracy AI, `docs/project-principles.md` — założenia projektu, a `docs/roadmap.md` — stan i plan prac.
- Roadmapę prowadź zwięźle: opisuj cel najbliższego etapu, aktualny stan, 3–5 najbliższych kroków, dalszy kierunek oraz istotne odłożone sprawy.
- Nie traktuj roadmapy jako historii commitów ani listy utworzonych plików i endpointów. Zakończone prace podsumowuj przez działające mechanizmy lub etapy. Nie powielaj postępu w innych dokumentach.
- Wyraźnie rozróżniaj „zaplanowane”, „rozpoczęte” i „ukończone”. Przy odłożonym problemie podaj powód lub moment powrotu. Roadmapa nie zastępuje odczytu aktualnego kodu.
- Ważne decyzje architektoniczne zapisuj osobno, gdy ich uzasadnienie będzie potrzebne w przyszłości.
- W podsumowaniu zmian wymień dodane, zmienione i usunięte pliki, również dokumentację. Wyjaśnij przeznaczenie dodatkowych plików i sposób sprawdzenia zmian.
- Nie wykonuj commitów ani pushowania bez polecenia. Commity robimy po spójnym fragmencie pracy lub dniu pracy, nie po każdej drobnej zmianie.
