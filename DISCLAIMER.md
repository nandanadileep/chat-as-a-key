# Disclaimer

**Last updated:** 2026-05-02 — link this file in issues, PRs, and discussions instead of re-stating terms in threads.

This document applies to **chat-as-a-key** (the “Software”): a self-hostable tool that uses browser automation (for example Playwright) to interact with third-party chat websites and related services.

**This is not legal advice.** If you use the Software for anything important—especially a business or a product you offer to others—you should have a qualified lawyer review your situation and the current terms of each service you touch.

---

## 1. No affiliation; no endorsement

The Software is an **independent, open-source project**. It is **not** affiliated with, sponsored by, or endorsed by:

- Anthropic (Claude.ai, Claude Pro, etc.)
- OpenAI (ChatGPT, etc.)
- Google (Gemini, etc.)
- xAI (Grok, etc.)
- Perplexity AI, Inc.
- Microsoft (Copilot, etc.)

Any product names are trademarks of their respective owners. Use of a name does not imply partnership or permission.

---

## 2. Not an official API; no vendor API keys

The Software does **not** issue **Anthropic**, **OpenAI**, **Google**, or other **official platform API keys**. It is **not** a substitute for each company’s documented APIs, consoles, or commercial agreements.

If you need guaranteed, contract-backed programmatic access, use each provider’s **official API and billing path**, not this project.

---

## 3. Your responsibility for terms, laws, and policies

By running the Software against a third-party service, **you** (and anyone on whose behalf you run it) are responsible for:

- That service’s **Terms of Service**, **Acceptable Use Policy**, and any other posted rules, **as they exist today and as they change**;
- **Applicable laws and regulations** in your jurisdiction (including export, privacy, and consumer rules where relevant).

**The authors and contributors of the Software do not warrant** that any particular use complies with those terms or laws. **Violation can result in enforcement against your account** (for example suspension or termination), loss of paid subscriptions without refund where the provider’s terms allow, or other actions the provider chooses to take.

---

## 4. Browser automation and “human” use

The Software may access third-party sites through **automated or scripted means** (e.g. controlled browsers, saved cookies, or storage state). Many consumer services **restrict or prohibit** automated access, scraping, or use outside their official clients or APIs, **except where they explicitly allow it**.

Whether your use is permitted is **between you and each provider**. Do not assume that self-hosting, personal use, or “only on my machine” exempts you from their rules.

---

## 5. Account and credential risk

- **Accounts:** Risk of **warnings, blocks, captchas, or permanent loss of access** to the underlying chat accounts typically falls on **whoever’s session or credentials** are used with the Software.
- **Credentials:** Cookies, storage state files, and browser profiles are **highly sensitive**. You are responsible for storing them securely, not committing them to git, and not sharing them in ways that violate the provider’s rules or compromise others.

---

## 6. Security and data

The Software may cause **prompts, page content, and network traffic** to flow through components you control (local server, browser, optional debug tooling). You are responsible for:

- Securing the host, ports, and any reverse proxy;
- Understanding what data leaves your machine to third-party services;
- Any **logging, tracing, or observability** features you enable.

The authors are **not** responsible for data you send to third parties or for misconfiguration that exposes secrets.

---

## 7. No warranty; limitation of liability

THE SOFTWARE IS PROVIDED **“AS IS”** AND **“AS AVAILABLE”**, WITHOUT WARRANTIES OF ANY KIND, WHETHER EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO IMPLIED WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE, OR NON-INFRINGEMENT.

TO THE MAXIMUM EXTENT PERMITTED BY APPLICABLE LAW, IN NO EVENT SHALL THE AUTHORS OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING BUT NOT LIMITED TO LOSS OF PROFITS, DATA, ACCOUNTS, GOODWILL, OR BUSINESS INTERRUPTION) ARISING OUT OF OR RELATED TO YOUR USE OF OR INABILITY TO USE THE SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGES.

Some jurisdictions do not allow certain limitations; in those cases, liability is limited to the fullest extent still permitted by law.

---

## 8. Changes

Third-party terms, products, and technical measures change often. This disclaimer may be updated from time to time. **Continuing to use the Software after changes means you accept the updated disclaimer** for the copy you use (check the repository or your distribution source).

---

## 9. Contact

For questions about **this repository** (bugs, features, documentation), use the project’s normal channels (e.g. GitHub issues). **Do not** use those channels for personal legal advice; consult a professional.
