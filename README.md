# Dhan Risk Management Bot

This repository contains a production-ready risk-management bot that:

- Fetches live capital from Dhan API (`GET /v2/user/margins`).
- Stops trading for the day if equity falls by 20% from day-start capital.
- Only trades between 09:25 and 15:00 (local server time).
- Automatically squares-off all positions at or after 15:00.
- Limits to **10 trades per day**.
- Persists state in SQLite (`bot_state.db`).

> **Important:** Replace Dhan environment variables with your own credentials before deploying.
