# Scenariusz 04 — CI failure: Maven BUILD FAILURE — brakujący artefakt w rejestrze

**Typ:** GitHub Issue  
**Repo:** `AGH-iot-agent/iot-agent-device-api`  
**Cel:** Sprawdzenie, czy agent wykryje błąd rozwiązywania zależności Maven w CI i zaproponuje przywrócenie poprzedniej wersji lub publikację artefaktu.

## Opis

Pipeline CI dla `iot-agent-device-api` kończy się błędem na etapie budowania Maven. Ostatni commit na `main` podniósł wersję zależności `iot-agent-common` z `2.0.1` na `2.1.0` w `pom.xml`, ale ta wersja nie została jeszcze opublikowana do rejestru pakietów. Scenariusz tworzy issue na GitHubie opisujące problem — agent powinien przeanalizować logi CI, zidentyfikować brakujący artefakt i zaproponować fix (revert lub publikacja artefaktu).

## Kroki

1. Uzupełnij `GH_TOKEN` w pliku `.env`
2. Uruchom: `bash scenario_04.sh`
3. Oczekiwane: agent analizuje logi CI, wskazuje brakujący artefakt, proponuje revert w `pom.xml` lub publikację artefaktu

## Pliki

- `scenario_04.sh` — tworzy GitHub Issue z opisem błędu CI
- `issue_body.md` — treść Issue z logiem Maven BUILD FAILURE
- `cleanup_04.sh` — zamyka wszystkie otwarte issues z etykietą `iot-devops-agent`
- `.env` — zmienne środowiskowe (`GH_TOKEN`)
