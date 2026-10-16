# AI Council

**Ask Claude, ChatGPT and Gemini the same question, see their answers side by side, and let them review and improve each other's answers, round after round.**

AI Council is a small local web app. It talks to each AI through its official command-line tool, so it runs on the subscriptions you already have (Claude Pro/Max, ChatGPT Plus/Pro, Google AI / Gemini). You don't need API keys and pay nothing extra.

---

## Features

- **Side-by-side answers.** One question goes to every model you select, and the answers appear in parallel columns.
- **Choose your models.** Turn each AI on or off and pick a model from its dropdown (e.g. Opus, Sonnet, GPT-6.1 Sol, Gemini 3.1 Pro), or choose **Other…** to type any model name.
- **Review & improve rounds.** Each model reads the others' latest answers, points out errors and missing points, admits where the others did better, and writes an improved answer. Run as many rounds as you like.
- **Final answer.** Pick one model to merge everything into a single best answer, with a short list of points the models still disagree on.
- **Token usage & limits.** Every answer shows its exact input/output tokens. The **Usage & limits** panel shows tokens per model for today, the last 7 days and all time. For Claude it also shows your real subscription limits (5-hour and weekly: % used, % left, time until reset).
- **Token estimates.** Before you click, the page estimates how many tokens the next question or review round will send to each model.
- **Lean prompts.** Claude and ChatGPT get a short assistant prompt instead of their built-in coding-agent instructions. That's about 10× fewer input tokens per Claude call and about 35% fewer per ChatGPT call, so your limits last longer.
- **Stop, Retry, Copy.** Stop ends all running requests right away (so they stop using your quota). Every answer has Retry and Copy buttons.
- **Export & restore.** Download the whole discussion as a Markdown file. The last discussion is restored if you reload the page.
- **Safe by design.** The AI tools run in an empty temporary folder with tools disabled or read-only, so they only answer and never touch your files. The server only listens on your own computer (`127.0.0.1`) and rejects requests from other websites, so a web page you visit can't use your subscriptions.
- **No dependencies.** The backend uses only the Python standard library, and the frontend is a single HTML file.

## How it works

```
 Browser (index.html)  ──►  server.py (localhost:8765)  ──►  claude  /  codex  /  gemini   (official CLIs)
```

1. **Round 1:** every selected model answers the question independently.
2. **Round 2+:** each model receives the question, its own previous answer and the others' answers. It replies with a `## Review` section and an `## Improved answer` section. Only the improved answers carry into the next round.
3. **Final:** the chosen model combines the latest answers into one.

## Requirements

- macOS or Linux with **Python 3.8+**
- The CLI for each AI you want to use, signed in with your account:

| AI | CLI | Install | Sign in |
|---|---|---|---|
| Claude | Claude Code | `curl -fsSL https://claude.ai/install.sh \| bash` | run `claude` once |
| ChatGPT | Codex CLI | `brew install codex` | run `codex` once, choose *Sign in with ChatGPT* |
| Gemini | Gemini CLI | `brew install gemini-cli` | run `gemini` once, choose *Login with Google* |

You don't need all three. The app shows which ones are installed (green dot) and how to install the rest.

## Getting started

```bash
git clone https://github.com/mj-amin-ece/ai-council.git
cd ai-council
python3 server.py
```

Then open **http://localhost:8765** in your browser.

## How to use

1. Tick the models you want and pick a model from each dropdown.
2. Type your question and click **Ask selected models** (or press <kbd>Cmd</kbd>+<kbd>Enter</kbd>).
3. Click **Review & improve** to let them critique each other and improve. Repeat for more rounds.
4. Choose who writes the final answer and click **Write final answer**.
5. Click **Export** to save the discussion as Markdown, or **New question** to start over.

Open **Usage & limits** to see your token use and remaining limits. Your model choices and the last discussion are remembered in the browser.

### What usage information each AI provides

| AI | Tokens per answer | Remaining subscription limit |
|---|---|---|
| Claude | Exact | Yes: 5-hour and weekly limits with reset time |
| ChatGPT | Exact | Not reported by the Codex CLI (run `codex`, then `/status`) |
| Gemini | Exact | Not reported by the Gemini CLI (Google uses daily request limits) |

Token history is saved locally in `usage.json` (excluded from git). **Clear history** in the panel resets it.

## Customising

| What | Where |
|---|---|
| Models in each dropdown | `PROVIDERS` in `server.py` |
| Review and final-answer prompts | `reviewPrompt` and `finalPrompt` in `index.html` |
| How each AI is called | `ask_claude`, `ask_chatgpt` and `ask_gemini` in `server.py` |
| Port / timeout | environment variables `PORT` (default `8765`) and `AI_TIMEOUT` (seconds, default `300`) |

To use an API key instead of a CLI for one of the AIs, only that model's `ask_…` function needs to change.

## Troubleshooting

- **A model shows "not installed".** Install its CLI (see the table above), then restart `server.py` and refresh the page.
- **"requires usage credits" or a rate-limit error.** Your subscription's limit for that model was reached. Choose a different model in the dropdown, or check **Usage & limits** to see when it resets.
- **Gemini: "Please set an Auth method".** Run `gemini` once in Terminal and choose *Login with Google*.
- **"model not found".** That model isn't available on your plan, or its name changed. Pick another one, or update the list in `server.py`.
- **Slow answers.** Each CLI takes a few seconds to start, and review rounds send longer prompts. Faster models (Haiku, GPT-6 Luna, Gemini Flash) help.

## Project structure

```
ai-council/
├── server.py   # local web server, one adapter per AI CLI, usage tracking
├── index.html  # the whole user interface
├── README.md
└── usage.json  # your token history (created on first use, not committed)
```

---

Developed by **Mohammad Javad Amin**.
If you have any questions, contact me: [aminm@usi.ch](mailto:aminm@usi.ch)
