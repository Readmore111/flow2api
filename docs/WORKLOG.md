# Worklog

## 2026-06-29 - Usage documentation and GitHub push

- Purpose: Add a user-facing setup guide for local image generation testing and push the local changes to GitHub.
- Files changed: `docs/IMAGE_GENERATION_USAGE.md`, `docs/WORKLOG.md`.
- Commands run:
  - `git status --short --branch`
  - `git remote -v`
  - `git -c http.proxy=http://127.0.0.1:7897 -c https.proxy=http://127.0.0.1:7897 fetch origin`
  - `git rev-list --left-right --count HEAD...origin/main`
  - `rg -n "__Secure-next-auth.session-token=[A-Za-z0-9]|Bearer [A-Za-z0-9_-]{20,}|AIzaSy[A-Za-z0-9_-]+|ghp_[A-Za-z0-9]|github_pat_|ya29\\.|127\\.0\\.0\\.1:9223" docs src static tests -S`
  - `.\venv\Scripts\python.exe -m unittest tests.test_api_captcha_fingerprint tests.test_flow_client_upload tests.test_flow_image_protocol -v`
  - `git diff --check`
- Verification result: Unit tests passed (6 tests). `git diff --check` passed with only Git line-ending warnings. `HEAD...origin/main` was `0 0` after fetch. Secret scan only matched the existing public Flow API key constant in `src/services/flow_client.py`; no live ST, AT, GitHub token, local database, log, cache, or 9223-only runtime artifact was found in the intended commit paths.
- Known risk or follow-up: GitHub access from this host requires the local proxy at `127.0.0.1:7897`, so push commands should use that proxy if direct access returns an empty response.

## 2026-06-29 - Local Flow image testing setup

- Purpose: Configure the local service so the user can test Google Flow image generation from the bundled web UI.
- Files changed: `src/services/flow_client.py`, `static/manage.html`, `static/test.html`, `tests/test_flow_image_protocol.py`, `docs/WORKLOG.md`.
- Commands run:
  - `git status --short --branch`
  - `Invoke-RestMethod -Uri http://127.0.0.1:8000/health`
  - `curl.exe -I -L --max-time 20 https://labs.google/auth/session`
  - `curl.exe -I -L --max-time 20 --proxy http://127.0.0.1:7897 https://labs.google/auth/session`
  - `.\venv\Scripts\python.exe -m unittest tests.test_api_captcha_fingerprint tests.test_flow_client_upload tests.test_flow_image_protocol -v`
  - `git diff --check`
  - Browser automation through Chrome 9223 against `http://127.0.0.1:8000/test`.
- Verification result: Unit tests passed (6 tests). `git diff --check` passed with only Git line-ending warnings. The service is reachable on `http://127.0.0.1:8000`, captcha mode is `personal`, one active image-enabled token is configured from the user's Chrome 9223 Flow session, and the `/test` page generated one image successfully with `gemini-3.1-flash-image-landscape` in about 54.5 seconds.
- Known risk or follow-up: Local Flow access depends on the user's proxy at `127.0.0.1:7897` and the imported browser session remaining valid. Video and Omni work are intentionally deferred per the user's instruction.
