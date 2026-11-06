# tech-news-agent

Pakiet **`tech_news_agent`** oraz zduplikowane **`travel_agent`** i **`gmail_agent`** (tech news importuje moduły fetch z `travel_agent` i wysyłkę z `gmail_agent`).

## Uruchomienie

```bash
cd ~/projects/agents/tech-news-agent
source .venv/bin/activate
python -m tech_news_agent send --no-browser
```

## launchd

```bash
./scripts/install_tech_news_schedule.sh
```

Szablon: `scripts/com.tech-news-agent.daily.plist`. Gotowy plik w `~/Library/LaunchAgents/disabled/` (w razie włączenia — skopiuj do `LaunchAgents` i `launchctl bootstrap`).
