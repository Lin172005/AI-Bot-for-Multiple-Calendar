import asyncio
import time
import difflib
import json
import os
import urllib.request
import urllib.parse
from urllib.parse import urlparse
from pathlib import Path
from dataclasses import dataclass
from typing import Optional
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError


def _load_env_files() -> None:
    try:
        from dotenv import load_dotenv  # type: ignore
    except Exception:
        return

    bot_root = Path(__file__).resolve().parents[1]  # .../bot
    for name in (".env", ".env.local"):
        p = bot_root / name
        if p.exists():
            load_dotenv(dotenv_path=p, override=False)


_load_env_files()

MEET_LINK = os.getenv("MEET_LINK", "https://meet.google.com/wam-mbqm-axy")
BACKEND_URL = os.getenv("BACKEND_URL", "http://localhost:5000/captions")
API_BASE_URL = os.getenv("API_BASE_URL", "").strip()
BOT_ID = os.getenv("BOT_ID", "")
HEADLESS = os.getenv("HEADLESS", "").strip().lower() in {"1", "true", "yes", "y", "on"}
CHAT_ON_JOIN = os.getenv("CHAT_ON_JOIN", "").strip()
LAST_SENT = {}  # dedupe finalized segments (key -> last timestamp)
AUTO_LEAVE_ALONE_SECONDS = float(os.getenv("AUTO_LEAVE_ALONE_SECONDS", "45"))
AUTO_LEAVE_ENABLED = os.getenv("AUTO_LEAVE_ENABLED", "1").strip().lower() in {"1", "true", "yes", "y", "on"}
AUTO_LEAVE_MIN_CAPTION_IDLE_SECONDS = float(os.getenv("AUTO_LEAVE_MIN_CAPTION_IDLE_SECONDS", "20"))

# Attendee-like segmentation behavior:
# - False (default): emit each stable fragment per speaker (less verbose, less grouping)
# - True: group consecutive fragments per speaker until a short pause
MERGE_CONSECUTIVE_CAPTIONS = False
CAPTION_IDLE_SECONDS = 2.0
# If merging is enabled, stable fragments from the same speaker within this gap
# are combined into one line.
MERGE_GAP_SECONDS = 1.0
# Meet updates captions every ~1–3s while someone is speaking.
# If we split too aggressively (e.g., 1s), we'll emit partial drafts repeatedly.
#
# - REVISION_WINDOW_SECONDS: treat updates within this gap as the same in-progress fragment.
# - FORCE_SPLIT_GAP_SECONDS: if we see a long gap, force a new utterance even if Meet
#   re-sends the full previous sentence as a prefix.
REVISION_WINDOW_SECONDS = 8.0
FORCE_SPLIT_GAP_SECONDS = 30.0
MAX_SEGMENT_SECONDS = 20.0
DEDUP_WINDOW_SECONDS = 2.0

CAPTIONS_LOG_PATH = Path(__file__).resolve().parent / "data" / "captions.log"


def _default_api_base() -> str:
    if API_BASE_URL:
        return API_BASE_URL.rstrip("/")
    # If BACKEND_URL points at /captions on the FastAPI backend, derive base.
    u = (BACKEND_URL or "").strip().rstrip("/")
    if u.endswith("/captions"):
        return u[: -len("/captions")]
    return ""


async def _emit_state(state: str) -> None:
    """Best-effort POST to backend to update bot state."""
    if not API_BASE_URL or not BOT_ID:
        return
    try:
        url = f"{_default_api_base()}/bots/{BOT_ID}/state"
        payload = json.dumps({"state": state}).encode("utf-8")
        req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"}, method="POST")
        await asyncio.to_thread(lambda: urllib.request.urlopen(req, timeout=10))  # nosec - internal service
    except Exception:
        pass


async def _open_chat_panel(page) -> bool:
    # If textbox exists → already open
    textbox = page.locator(
        'textarea[jsname="YPqjbf"], textarea[aria-label*="Send a message" i]'
    ).first

    if await textbox.count() > 0:
        return True

    # Force toolbar to stay visible
    vp = page.viewport_size or {"width": 1280, "height": 720}

    # Move mouse to extreme bottom center
    await page.mouse.move(vp["width"] // 2, vp["height"] - 2)
    await asyncio.sleep(0.3)

    toggle = page.locator(
        'button[jsname="A5il2e"][data-panel-id="2"]'
    ).first

    if await toggle.count() == 0:
        print("[WARN] Chat toggle not found")
        return False

    try:
        await toggle.click(force=True)
        await asyncio.sleep(0.8)
    except Exception as e:
        print("[WARN] Toggle click failed:", e)
        return False

    # Confirm panel opened
    if await textbox.count() > 0:
        return True

    print("[WARN] Chat panel did not open after click")
    return False


async def _open_people_panel(page) -> bool:
    """Open the People/Participants panel in Meet."""
    await _wake_meet_controls(page)
    selectors = [
        'button[aria-label*="Show everyone" i]',
        'button[aria-label*="People" i]',
        'button[aria-label*="Participants" i]',
        'div[role="button"][aria-label*="Show everyone" i]',
        '[data-tooltip*="Show everyone" i]',
        '[data-tooltip*="People" i]',
        '[data-tooltip*="Participants" i]',
        'button:has(i.google-symbols:has-text("group"))',
        'button:has(i.google-symbols:has-text("groups"))',
    ]
    for sel in selectors:
        try:
            loc = page.locator(sel).first
            if await loc.count() == 0:
                continue
            try:
                await loc.wait_for(state="visible", timeout=1500)
            except Exception:
                pass
            try:
                await loc.scroll_into_view_if_needed()
            except Exception:
                pass
            try:
                await loc.click(timeout=1500)
            except Exception:
                try:
                    await loc.click(timeout=1500, force=True)
                except Exception:
                    continue
            await asyncio.sleep(0.4)
            # Try to detect panel visible via common container hints
            try:
                panel = page.locator('[role="dialog"]:has-text("People"), [aria-label*="People" i]').first
                if await panel.count() > 0:
                    return True
            except Exception:
                pass
            # If toggle shows expanded, treat as open
            try:
                toggle = page.locator('button[aria-expanded="true"]').first
                if await toggle.count() > 0:
                    return True
            except Exception:
                pass
        except Exception:
            continue
    return False


async def _inject_user_manager(page) -> None:
    """Inject a lightweight userManager into the Meet page to track participants.

    This mimics the idea of a JS payload that keeps a current users map updated by
    scanning the People panel DOM periodically.
    """
    try:
        script = """
        (() => {
            if (window.userManager) return;
            const manager = {
                allUsersMap: new Map(),
                currentUsersMap: new Map(),
                getCurrentUsersInMeeting() { return Array.from(this.currentUsersMap.values()); },
                getUserByFullName(name) { return this.currentUsersMap.get(name) || this.allUsersMap.get(name); },
                getUserByDeviceId(id) { return null; }
            };

            function refreshFromDom() {
                try {
                    const items = Array.from(document.querySelectorAll('[data-member-id], [role="listitem"]'));
                    const curr = new Map();
                    for (const el of items) {
                        let fullName = '';
                        const nameEl = el.querySelector('[aria-label]') || el.querySelector('[data-name]') || el.querySelector('[role="button"]');
                        if (nameEl && nameEl.getAttribute('aria-label')) fullName = nameEl.getAttribute('aria-label');
                        if (!fullName) fullName = (el.textContent || '').trim();
                        if (fullName) {
                            const rec = { fullName, active: true };
                            curr.set(fullName, rec);
                            manager.allUsersMap.set(fullName, rec);
                        }
                    }
                    manager.currentUsersMap = curr;
                } catch (e) {}
            }

            window.userManager = manager;
            refreshFromDom();
            setInterval(refreshFromDom, 1500);
        })();
        """
        await page.add_init_script(script)
        # Also execute once in case init_script doesn't run for existing context
        await page.evaluate(script)
    except Exception:
        pass


async def _get_current_users_count(page) -> Optional[int]:
    try:
        val = await page.evaluate(
            """
                () => {
                    const um = window.userManager;
                    if (!um || typeof um.getCurrentUsersInMeeting !== 'function') return null;
                    const list = um.getCurrentUsersInMeeting();
                    return Array.isArray(list) ? list.length : null;
                }
            """
        )
        if val is None:
            return None
        try:
            return int(val)
        except Exception:
            return None
    except Exception:
        return None


async def _get_dom_contributors_count(page) -> Optional[int]:
    try:
        val = await page.evaluate(
            """
            () => {
                const el = document.querySelector('[data-avatar-count]');
                if (!el) return null;

                const count = el.getAttribute('data-avatar-count');
                return count ? parseInt(count, 10) : null;
            }
            """
        )

        if val is None:
            return None

        return int(val)

    except Exception:
        return None



async def _debug_chat_dom(page) -> None:
    try:
        info = await page.evaluate(
            """() => {
                const count = (sel) => document.querySelectorAll(sel).length;
                const takeAttrs = (sel) => {
                    const el = document.querySelector(sel);
                    if (!el) return null;
                    const attrs = {};
                    for (const a of el.attributes) attrs[a.name] = a.value;
                    return { tag: el.tagName, attrs };
                };
                return {
                    inputCount: count('textarea[jsname="YPqjbf"]'),
                    editableCount: count('div[contenteditable="true"], [contenteditable="true"][role="textbox"]'),
                    sendBtnCount: count('button[jsname="SoqoBf"], [role="button"][jsname="SoqoBf"]'),
                    chatBtnCount: count('button[jsname="A5il2e"], button[aria-label*="Chat" i], [role="button"][aria-label*="Chat" i], [data-tooltip*="Chat" i]'),
                    chatEveryoneCount: count('button[jsname="A5il2e"][aria-label="Chat with everyone" i]'),
                    chatExpandedCount: count('button[jsname="A5il2e"][aria-expanded="true"]'),
                    chatPanelCount: count('#ME4pNd, [id="ME4pNd"]'),
                    sampleInput: takeAttrs('textarea[jsname="YPqjbf"], textarea[aria-label*="Send a message" i]'),
                    sampleEditable: takeAttrs('[contenteditable="true"][role="textbox"], div[contenteditable="true"]'),
                    sampleSendBtn: takeAttrs('button[jsname="SoqoBf"], [role="button"][jsname="SoqoBf"]'),
                    sampleChatBtn: takeAttrs('button[jsname="A5il2e"][aria-label*="Chat" i], button[aria-label*="Chat" i]'),
                    sampleChatEveryoneBtn: takeAttrs('button[jsname="A5il2e"][data-panel-id="2"], button[jsname="A5il2e"][aria-label*="Chat with everyone" i]'),
                };
            }"""
        )
        print(f"[CHAT DEBUG] {info}")
    except Exception as e:
        print(f"[CHAT DEBUG] failed: {e}")


async def _send_chat_message(page, message: str) -> bool:
    msg = (message or "").strip()
    if not msg:
        return False
    
    # If chat is already open, don't toggle it; otherwise open it.
    async def _is_chat_open_local() -> bool:
        try:
            panel_loc = page.locator('#ME4pNd, [id="ME4pNd"]').first
            input_loc = page.locator('textarea[jsname="YPqjbf"], textarea[aria-label*="Send a message" i]').first
            if await panel_loc.count() > 0 and await panel_loc.first.is_visible():
                return True
            if await input_loc.count() > 0 and await input_loc.first.is_visible():
                return True
        except Exception:
            pass
        return False
    
    if not await _is_chat_open_local():
        if not await _open_chat_panel(page):
            # One more quick keyboard fallback try directly from sender
            try:
                for m1, m2, key in [("Control", "Alt", "KeyC"), ("Control", "Alt", "KeyM")]:
                    await page.keyboard.down(m1); await page.keyboard.down(m2)
                    await page.keyboard.press(key)
                    await page.keyboard.up(m2); await page.keyboard.up(m1)
                    for _ in range(10):
                        if await _is_chat_open_local():
                            break
                        await asyncio.sleep(0.1)
                    if await _is_chat_open_local():
                        break
            except Exception:
                pass
        if not await _is_chat_open_local():
            print("[WARN] Chat panel not opened")
            await _debug_chat_dom(page)
            return False


    # Prefer exact Meet chat textarea you pasted. Some Meet builds use a contenteditable textbox.
    textbox_selectors = [
        'textarea[jsname="YPqjbf"]',
        'textarea[aria-label="Send a message" i]',
        'textarea[placeholder*="Send a message" i]',
        '[contenteditable="true"][role="textbox"]',
        'div[contenteditable="true"]',
        'textarea',
    ]

    textbox = None
    for sel in textbox_selectors:
        try:
            loc = page.locator(sel).first
            if await loc.count() == 0:
                continue
            if not HEADLESS:
                if not await loc.is_visible():
                    continue
            textbox = loc
            break
        except Exception:
            continue

    if textbox is None:
        print("[WARN] Chat textbox not found")
        await _debug_chat_dom(page)
        return False

    async def _textbox_value() -> str:
        try:
            tag = (await textbox.evaluate("(el) => el.tagName")) or ""
            if str(tag).upper() == "TEXTAREA":
                return (await textbox.input_value()) or ""
        except Exception:
            pass
        try:
            return (await textbox.evaluate("(el) => (el.textContent || '')")) or ""
        except Exception:
            return ""

    try:
        await textbox.click(force=True)
        # Type like a user so Meet's jsaction input handlers fire reliably.
        try:
            await page.keyboard.press("Control+A")
            await page.keyboard.press("Backspace")
        except Exception:
            pass
        await page.keyboard.type(msg, delay=15)
        await asyncio.sleep(0.25)
    except Exception:
        print("[WARN] Failed to type into chat textbox")
        await _debug_chat_dom(page)
        return False

    # Primary send method: Enter.
    # In Meet chat this usually sends (Shift+Enter is newline).
    try:
        await page.keyboard.press("Enter")
        await asyncio.sleep(0.4)
        after = (await _textbox_value()).strip()
        if not after:
            print(f"[OK] Sent chat message via Enter ({len(msg)} chars)")
            return True
    except Exception:
        pass

    # Click the send button you pasted (jsname=SoqoBf) once it becomes enabled.
    send_selectors = [
        'button[jsname="SoqoBf"][aria-label*="Send a message" i]',
        '[role="button"][jsname="SoqoBf"][aria-label*="Send a message" i]',
        'button[aria-label*="Send a message" i]',
        '[role="button"][aria-label*="Send a message" i]',
    ]

    for sel in send_selectors:
        try:
            btn = page.locator(sel).first
            if await btn.count() == 0:
                continue
            # Wait briefly for disabled="" to be removed.
            try:
                await btn.wait_for(state="visible", timeout=2500)
            except Exception:
                continue

            # Poll until enabled (Meet initially renders disabled until input event).
            enabled = False
            for _ in range(30):
                disabled = await btn.get_attribute("disabled")
                if disabled is None:
                    enabled = True
                    break
                await asyncio.sleep(0.1)

            if not enabled:
                continue

            await btn.click(force=True)
            await asyncio.sleep(0.4)
            after = (await _textbox_value()).strip()
            if not after:
                print(f"[OK] Sent chat message via send button ({len(msg)} chars)")
                return True
            # Sometimes the textbox doesn't clear even when sent; accept best-effort.
            print(f"[OK] Clicked send button ({len(msg)} chars)")
            return True
        except Exception:
            continue

    print("[WARN] Send button not clickable")
    await _debug_chat_dom(page)
    # DOM fallback: set value and click send programmatically.
    try:
        ok = await page.evaluate(
            """
            (text) => {
              const input = document.querySelector('textarea[jsname="YPqjbf"], textarea[aria-label*="Send a message" i], [contenteditable="true"][role="textbox"], div[contenteditable="true"]');
              if (!input) return false;
              const isTA = (input.tagName || '').toUpperCase() === 'TEXTAREA';
              if (isTA) { input.value = text; }
              else { input.textContent = text; }
              try { input.dispatchEvent(new Event('input', { bubbles: true })); } catch(e) {}
              try { input.dispatchEvent(new Event('change', { bubbles: true })); } catch(e) {}
              const btn = document.querySelector('button[jsname="SoqoBf"], [role="button"][jsname="SoqoBf"], button[aria-label*="Send a message" i], [role="button"][aria-label*="Send a message" i]');
              if (btn) { try { btn.click(); return true; } catch(e) {} }
              return true;
            }
            """,
            msg,
        )
        await asyncio.sleep(0.6)
        after = (await _textbox_value()).strip()
        if not after:
            print(f"[OK] Sent chat message via DOM fallback ({len(msg)} chars)")
            return True
        else:
            print(f"[WARN] DOM fallback attempted; textbox still not empty")
    except Exception:
        pass
    return False


async def _command_poll_loop(page) -> None:
    # Long-poll the backend for commands (e.g., send chat).
    if not BOT_ID:
        return
    base = _default_api_base()
    if not base:
        return

    url = f"{base}/bots/{BOT_ID}/commands/next"
    while True:
        try:
            def _fetch() -> tuple[int, str]:
                full_url = url
                try:
                    full_url = f"{url}?{urllib.parse.urlencode({'timeout': 25})}"
                except Exception:
                    pass
                req = urllib.request.Request(full_url, method="GET")
                try:
                    with urllib.request.urlopen(req, timeout=30) as resp:  # nosec - internal service
                        status = getattr(resp, "status", None) or resp.getcode()
                        body = resp.read().decode("utf-8", errors="replace")
                        return int(status), body
                except Exception:
                    return 0, ""

            status_code, body = await asyncio.to_thread(_fetch)
            if status_code != 200:
                await asyncio.sleep(1)
                continue
            try:
                data = json.loads(body) if body else {}
            except Exception:
                data = {}
            cmd = (data or {}).get("command")
            if not cmd:
                continue
            if cmd.get("type") == "chat":
                await _send_chat_message(page, cmd.get("text") or "")
        except Exception:
            await asyncio.sleep(1)


async def _is_alone(page) -> bool:
    try:
        # Read participant badge directly
        count = await page.evaluate("""
            () => {
                const btn = document.querySelector(
                    'button[aria-label*="Show everyone" i], ' +
                    'button[aria-label*="People" i], ' +
                    'button[aria-label*="Participants" i]'
                );
                if (!btn) return null;

                const label = btn.getAttribute("aria-label") || "";
                const match = label.match(/\\d+/);
                if (!match) return null;

                return parseInt(match[0], 10);
            }
        """)

        if isinstance(count, int):
            print("[INFO] Badge count:", count)
            return count <= 1

    except Exception:
        pass

    return False



async def _leave_call(page) -> bool:
    """Click the leave/hang up button and close the page."""
    selectors = [
        'button[aria-label*="Leave call" i]',
        'button[aria-label*="Leave" i]',
        'button[aria-label*="End call" i]',
        'button:has(i.google-symbols:has-text("call_end"))',
    ]
    for sel in selectors:
        try:
            btn = page.locator(sel).first
            if await btn.count() == 0:
                continue
            await btn.scroll_into_view_if_needed()
            await btn.click(force=True)
            return True
        except Exception:
            continue
    # Keyboard fallback: Shift+Ctrl+H sometimes maps to hang up (varies)
    try:
        await page.keyboard.down("Shift"); await page.keyboard.down("Control"); await page.keyboard.press("KeyH"); await page.keyboard.up("Control"); await page.keyboard.up("Shift")
        return True
    except Exception:
        return False


async def _monitor_alone_and_leave(page) -> None:
    alone_since = None

    while True:
        try:
            # Use the data-avatar-count attribute
            count = await _get_dom_contributors_count(page)
            print("[LEAVE CHECK] avatar count =", count)

            if count == 1:
                if alone_since is None:
                    alone_since = time.time()
                elif time.time() - alone_since >= AUTO_LEAVE_ALONE_SECONDS:
                    print("[INFO] Alone detected (count=1). Leaving meeting.")

                    try:
                        await _emit_state("ended")
                    except Exception:
                        pass

                    await _leave_call(page)
                    await asyncio.sleep(1)

                    try:
                        await page.context.close()
                    except Exception:
                        pass
                    return
            else:
                alone_since = None

        except Exception as e:
            print("Leave monitor error:", e)

        await asyncio.sleep(5)



async def _wake_meet_controls(page) -> None:
    # Meet hides the bottom control bar until the mouse moves.
    try:
        vp = page.viewport_size or {"width": 1280, "height": 720}
        await page.mouse.move(vp["width"] // 2, vp["height"] // 2)
        await asyncio.sleep(0.05)
        await page.mouse.move(vp["width"] // 2, max(5, vp["height"] - 12))
        await asyncio.sleep(0.1)
    except Exception:
        pass

    # Close transient popovers/menus that can block clicks.
    try:
        # Do NOT press Escape if the chat panel appears to be open; Escape would close it.
        chat_panel = page.locator('#ME4pNd, [id="ME4pNd"]').first
        chat_input = page.locator('textarea[jsname="YPqjbf"], textarea[aria-label*="Send a message" i]').first
        chat_open = False
        try:
            if await chat_panel.count() > 0 and await chat_panel.first.is_visible():
                chat_open = True
        except Exception:
            pass
        if not chat_open:
            try:
                if await chat_input.count() > 0 and await chat_input.first.is_visible():
                    chat_open = True
            except Exception:
                pass
        if not chat_open:
            await page.keyboard.press("Escape")
    except Exception:
        pass

async def _click_if_visible(page, selector: str):
    try:
        loc = page.locator(selector)
        if await loc.count() == 0:
            return False

        first = loc.first
        # In headless mode Playwright's visibility heuristics can be strict; allow clicking regardless.
        if not HEADLESS:
            if not await first.is_visible():
                return False

        await first.scroll_into_view_if_needed()
        try:
            await first.click(timeout=1500)
        except Exception:
            # Meet sometimes has overlays/tooltips; force click as a last resort.
            await first.click(timeout=1500, force=True)
        return True
    except Exception:
        pass
    return False

async def _dom_click(page, selector: str) -> bool:
    """Best-effort DOM click via evaluate; useful in headless when overlays block clicks."""
    try:
        ok = await page.evaluate(
            """
            (sel) => {
                const el = document.querySelector(sel);
                if (!el) return false;
                try { el.click(); return true; } catch(e) { return false; }
            }
            """,
            selector,
        )
        return bool(ok)
    except Exception:
        return False


async def _dismiss_got_it_popup(page) -> bool:
    """Dismiss the blocking onboarding/information card with a 'Got it' button.

    This appears when multiple participants join quickly; it can block clicks
    for captions and chat until dismissed.
    """
    await _wake_meet_controls(page)
    # Try common selectors and role-based queries first
    selectors = [
        'button:has-text("Got it")',
        'div[role="dialog"] button:has-text("Got it")',
        'button[data-mdc-dialog-action="ok"]',
        'div[role="dialog"] [data-mdc-dialog-action="ok"]',
    ]
    for sel in selectors:
        try:
            loc = page.locator(sel).first
            if await loc.count() == 0:
                continue
            try:
                await loc.scroll_into_view_if_needed()
            except Exception:
                pass
            try:
                await loc.click(timeout=1200)
            except Exception:
                try:
                    await loc.click(timeout=1200, force=True)
                except Exception:
                    # DOM fallback
                    await _dom_click(page, sel)
            await asyncio.sleep(0.2)
            print('[UI] Dismissed "Got it" popup')
            return True
        except Exception:
            continue

    # Fallback: find the span text and click the closest ancestor button via JS
    try:
        ok = await page.evaluate(
            """
            () => {
                const spans = Array.from(document.querySelectorAll('span.VfPpkd-vQzf8d'));
                for (const sp of spans) {
                    const txt = (sp.textContent || '').trim().toLowerCase();
                    if (txt === 'got it') {
                        let el = sp;
                        for (let i = 0; i < 6 && el; i++) {
                            if (el.tagName && el.tagName.toLowerCase() === 'button') break;
                            el = el.parentElement;
                        }
                        if (el && el.tagName && el.tagName.toLowerCase() === 'button') {
                            try { el.click(); return true; } catch (e) { /* ignore */ }
                        }
                    }
                }
                // Generic dialog default-action buttons
                const okBtn = document.querySelector('button[data-mdc-dialog-action="ok"]');
                if (okBtn) { try { okBtn.click(); return true; } catch(e) {} }
                return false;
            }
            """
        )
        if ok:
            await asyncio.sleep(0.2)
            print('[UI] Dismissed "Got it" popup (DOM)')
            return True
    except Exception:
        pass

    # Role-based query as last attempt
    try:
        btn = page.get_by_role("button", name="Got it")
        if await btn.count() > 0:
            try:
                await btn.first.click()
            except Exception:
                try:
                    await btn.first.click(force=True)
                except Exception:
                    pass
            await asyncio.sleep(0.2)
            print('[UI] Dismissed "Got it" popup (role)')
            return True
    except Exception:
        pass

    return False


@dataclass
class Segment:
    combined: str
    frag: str
    started_at: float
    updated_at: float


class CaptionSegmenter:
    """Attendee-like segmenter.

    - Incremental mode (merge_consecutive=False): Keep a per-speaker draft fragment.
      Emit only after it is stable (no updates) for `idle_seconds`.

    - Grouped mode (merge_consecutive=True): Keep a per-speaker `combined` buffer +
      draft `frag`. When Meet starts a new fragment, append the previous `frag` to
      `combined`. Emit only after a short pause.
    """

    def __init__(
        self,
        *,
        merge_consecutive: bool,
        idle_seconds: float,
        merge_gap_seconds: float,
        max_segment_seconds: float,
        revision_window_seconds: float,
        force_split_gap_seconds: float,
    ) -> None:
        self.merge_consecutive = merge_consecutive
        self.idle_seconds = idle_seconds
        self.merge_gap_seconds = merge_gap_seconds
        self.max_segment_seconds = max_segment_seconds
        self.revision_window_seconds = revision_window_seconds
        self.force_split_gap_seconds = force_split_gap_seconds

        self._lock = asyncio.Lock()
        self._segments: dict[str, Segment] = {}
        # Completed (finalized) fragments waiting for the idle timer to expire.
        self._completed: list[tuple[str, str, float]] = []

    @staticmethod
    def _norm(s: str) -> str:
        s = (s or "").lower()
        out: list[str] = []
        last_space = False
        for ch in s:
            if ch.isalnum():
                out.append(ch)
                last_space = False
            else:
                if not last_space:
                    out.append(' ')
                    last_space = True
        return ''.join(out).strip()

    @classmethod
    def _should_merge(cls, prev: str, curr: str) -> bool:
        """True when `curr` is a revision/update of the same fragment as `prev`."""
        if not prev:
            return True
        if not curr:
            return False

        if curr.startswith(prev) or prev.startswith(curr):
            return True

        p = cls._norm(prev)
        c = cls._norm(curr)
        if not p or not c:
            return True
        if c.startswith(p) or p.startswith(c):
            return True
        if p in c or c in p:
            return True

        tail = p[-24:] if len(p) > 24 else p
        if tail and tail in c:
            return True

        try:
            return difflib.SequenceMatcher(None, p, c).ratio() >= 0.80
        except Exception:
            return False

    @staticmethod
    def _segment_text(seg: Segment) -> str:
        combined = (seg.combined or '').strip()
        frag = (seg.frag or '').strip()
        if combined and frag:
            return f"{combined} {frag}".strip()
        return (combined or frag).strip()

    async def update(
        self,
        *,
        speaker: str,
        text: str,
        ts: float,
    ) -> None:
        """Update the per-speaker sticky-note draft.

        Never emits directly. Emission happens only via `flush_ready()` when the
        draft has not changed for `idle_seconds`.
        """
        if not text:
            return

        now = time.time()

        async with self._lock:
            seg = self._segments.get(speaker)
            if not seg:
                self._segments[speaker] = Segment(
                    combined="",
                    frag=text,
                    started_at=now,
                    updated_at=now,
                )
                return

            gap = now - seg.updated_at

            # Long gap: force a split even if the new text is a prefix-growth resend.
            if gap > self.force_split_gap_seconds:
                prev = self._segment_text(seg)
                if prev:
                    self._completed.append((speaker, prev, seg.updated_at))
                self._segments[speaker] = Segment(combined="", frag=text, started_at=now, updated_at=now)
                return

            if gap <= self.revision_window_seconds and self._should_merge(seg.frag, text):
                seg.frag = text
                seg.updated_at = now
                return

            # If it's outside the revision window and doesn't look like a revision, split.
            if gap > self.revision_window_seconds and not self._should_merge(seg.frag, text):
                prev = self._segment_text(seg)
                if prev:
                    self._completed.append((speaker, prev, seg.updated_at))
                self._segments[speaker] = Segment(combined="", frag=text, started_at=now, updated_at=now)
                return

            # New fragment detected.
            if self.merge_consecutive:
                # Merge consecutive stable fragments into one line if the pause is short.
                # Otherwise, finalize the current line and start a new one.
                if gap > self.merge_gap_seconds:
                    prev = self._segment_text(seg)
                    if prev:
                        self._completed.append((speaker, prev, seg.updated_at))
                    self._segments[speaker] = Segment(combined="", frag=text, started_at=now, updated_at=now)
                    return

                frag = (seg.frag or '').strip()
                if frag:
                    combined = (seg.combined or '').strip()
                    seg.combined = f"{combined} {frag}".strip() if combined else frag
                seg.frag = text
                seg.updated_at = now
                return

            # Incremental mode: keep only latest draft; emit later via idle flush.
            seg.frag = text
            seg.updated_at = now
            return

    async def flush_ready(self) -> list[tuple[str, str, float]]:
        """Emit any segments that have been stable long enough."""
        now = time.time()
        to_flush: list[tuple[str, str, float]] = []

        async with self._lock:
            # First flush completed fragments once they've sat unchanged for idle_seconds.
            if self._completed:
                remaining: list[tuple[str, str, float]] = []
                for spk, txt, t_updated in self._completed:
                    if (now - t_updated) >= self.idle_seconds:
                        to_flush.append((spk, txt, t_updated))
                    else:
                        remaining.append((spk, txt, t_updated))
                self._completed = remaining

            for spk, seg in list(self._segments.items()):
                stable = (now - seg.updated_at) >= self.idle_seconds
                too_long = (now - seg.started_at) >= self.max_segment_seconds
                if stable or too_long:
                    to_flush.append((spk, self._segment_text(seg), seg.updated_at))
                    self._segments.pop(spk, None)

        return to_flush

async def _enable_captions(page):
    # Try to find captions toggle in the bottom bar or more options menu.
    # Important: avoid clicking a generic "Captions" button if captions are already ON,
    # because that would turn them OFF.

    if await _captions_on(page):
        return True

    await _wake_meet_controls(page)
    try:
        # Popups can block caption controls; dismiss if present
        for _ in range(2):
            if await _dismiss_got_it_popup(page):
                break
            await asyncio.sleep(0.2)
    except Exception:
        pass

    # 1) Prefer explicit "Turn on ..." buttons (won't toggle off)
    explicit_on_selectors = [
        # Exact match for the element you pasted (it is a <button> but also has role="button")
        'button[aria-label="Turn on captions" i]',
        '[role="button"][aria-label="Turn on captions" i]',
        '[jsname="r8qRAd"][aria-label*="Turn on captions" i]',
        'button[aria-label*="Turn on captions" i]',
        'button[aria-label*="Turn on subtitles" i]',
        '[role="button"][aria-label*="Turn on subtitles" i]',
        'button[aria-label*="Show captions" i]',
        'button[aria-label*="Show subtitles" i]',
    ]
    for sel in explicit_on_selectors:
        # Try normal click, then DOM fallback
        if await _click_if_visible(page, sel) or await _dom_click(page, sel):
            await asyncio.sleep(0.8)
            if await _captions_on(page):
                print(f"[OK] Captions enabled via: {sel}")
                return True

    # 2) If we only have a generic captions control, click it only when it's not pressed.
    generic_controls = [
        'button[aria-label*="Captions" i]',
        'button[aria-label*="Subtitles" i]',
        'div[role="button"][data-tooltip*="Captions" i]',
        'div[role="button"][data-tooltip*="Subtitles" i]',
        '[data-tooltip*="Captions" i]',
        '[data-tooltip*="Subtitles" i]',
    ]
    for sel in generic_controls:
        try:
            loc = page.locator(sel).first
            if await loc.count() == 0:
                continue
            await _wake_meet_controls(page)
            pressed = await loc.get_attribute("aria-pressed")
            if pressed is not None and pressed.lower() == "true":
                continue
            try:
                await loc.click()
            except Exception:
                # DOM fallback
                await _dom_click(page, sel)
            await asyncio.sleep(0.6)
            if await _captions_on(page):
                print(f"[OK] Captions enabled via generic control: {sel}")
                return True
        except Exception:
            continue

    # Some layouts open a panel/menu after clicking captions; try toggling within (English UI)
    if await _click_if_visible(page, 'div[role="menuitem"]:has-text("Turn on captions")'):
        await asyncio.sleep(0.6)
        if await _captions_on(page):
            print("[OK] Captions enabled via panel switch")
            return True

    # 2) Via the three-dots More options menu
    if await _click_if_visible(page, 'button[aria-label*="More options" i]'):
        await asyncio.sleep(0.4)
        # Open the captions sub-menu/item
        if await _click_if_visible(page, 'div[role="menuitem"]:has-text("Captions")') or await _dom_click(page, 'div[role="menuitem"]:has-text("Captions")'):
            await asyncio.sleep(0.3)
            # Try toggling on and selecting English if present
            toggled = await _click_if_visible(page, 'div[role="menuitem"]:has-text("Turn on captions")') or await _dom_click(page, 'div[role="menuitem"]:has-text("Turn on captions")')
            lang_set = False
            for lang in ["English", "English (US)", "English (UK)"]:
                if await _click_if_visible(page, f'div[role="menuitem"]:has-text("{lang}")') or await _dom_click(page, f'div[role="menuitem"]:has-text("{lang}")'):
                    print(f"Caption language selected: {lang}")
                    lang_set = True
                    break
            if toggled:
                print("Captions enabled via More options menu")
                return True
            if lang_set:
                return True

    # 3) Via Activities panel (some Meet layouts place Captions here)
    try:
        # Open Activities
        if await _click_if_visible(page, 'button[aria-label*="Activities" i]') or await _click_if_visible(page, '[data-tooltip*="Activities" i]'):
            await asyncio.sleep(0.5)
            # Select Captions/Live captions tile
            for tile in [
                'div[role="menuitem"]:has-text("Captions")',
                'div[role="menuitem"]:has-text("Live captions")',
                'div[role="menuitem"]:has-text("Subtitles")',
            ]:
                if await _click_if_visible(page, tile):
                    await asyncio.sleep(0.5)
                    # Toggle ON within the activity panel
                    for opt in [
                        'div[role="menuitem"]:has-text("Turn on captions")',
                        'button[aria-label*="Turn on captions" i]',
                        'button[aria-label*="Turn on subtitles" i]',
                        'div[role="switch"][aria-checked="false"]',
                    ]:
                        if await _click_if_visible(page, opt):
                            await asyncio.sleep(0.6)
                            if await _captions_on(page):
                                print("[OK] Captions enabled via Activities panel")
                                return True
                        else:
                            if await _dom_click(page, opt):
                                await asyncio.sleep(0.6)
                                if await _captions_on(page):
                                    print("[OK] Captions enabled via Activities panel (DOM)")
                                    return True
                    # If language choices appear, prefer English
                    for lang in ["English", "English (US)", "English (UK)"]:
                        if await _click_if_visible(page, f'div[role="menuitem"]:has-text("{lang}")'):
                            await asyncio.sleep(0.3)
                            if await _captions_on(page):
                                print("[OK] Captions enabled via Activities language selection")
                                return True
                        else:
                            if await _dom_click(page, f'div[role="menuitem"]:has-text("{lang}")'):
                                await asyncio.sleep(0.3)
                                if await _captions_on(page):
                                    print("[OK] Captions enabled via Activities language selection (DOM)")
                                    return True
    except Exception:
        pass

    return False


async def _captions_region_present(page) -> bool:
    # Be strict: Meet has many unrelated aria-live nodes (mic/camera status, toasts).
    # Only treat captions as present when the actual captions container exists.
    selectors = [
        'div[role="region"][aria-label*="Captions" i]',
        'div[role="region"][aria-label*="Subtitles" i]',
        # Older/alternate container
        '[jsname="YSxPC"]',
        # Caption blocks (usually inside the captions region)
        '.nMcdL',
    ]
    for sel in selectors:
        try:
            if await page.locator(sel).count() > 0:
                return True
        except Exception:
            pass
    return False


async def _captions_on(page) -> bool:
    try:
        if await page.locator('button[aria-label*="Turn off captions" i], button[aria-label*="Turn off subtitles" i]').count() > 0:
            return True
    except Exception:
        pass
    return await _captions_region_present(page)


async def _try_caption_shortcuts(page) -> None:
    # Meet's caption shortcut is typically "c". Some environments/layouts have variants,
    # so we try a small set, safely.
    try:
        await page.evaluate(
            """() => {
                const el = document.activeElement;
                if (el && (el.tagName === 'INPUT' || el.tagName === 'TEXTAREA' || el.isContentEditable)) {
                    try { el.blur(); } catch (e) {}
                }
            }"""
        )
    except Exception:
        pass

    for combo in [
        ("KeyC", None),
        ("KeyC", "Shift"),
    ]:
        key, mod = combo
        try:
            if mod:
                await page.keyboard.down(mod)
            await page.keyboard.press(key)
            if mod:
                await page.keyboard.up(mod)
            await asyncio.sleep(0.4)
        except Exception:
            continue


async def _ensure_captions_on(page, attempts: int = 10) -> bool:
    # Retry loop: check state, dismiss popups, try explicit enable clicks, then shortcut.
    for _ in range(attempts):
        await _wake_meet_controls(page)
        
        try:
            await _dismiss_got_it_popup(page)
        except Exception:
            pass
        if await _captions_on(page):
            return True

        # Click-based enable attempts (safe against turning OFF)
        if await _enable_captions(page):
            if await _captions_on(page):
                return True

        # Keyboard shortcuts ("c" is the common one)
        await _try_caption_shortcuts(page)
        if await _captions_on(page):
            return True

        await asyncio.sleep(0.8)

    return await _captions_on(page)

async def _monitor_meeting_end(page) -> None:
    """Detect end-of-meeting screens and finalize/close if seen."""
    end_texts = [
        "You left the meeting",
        "You’ve left the call",
        "You have left the call",
        "Call ended",
        "This meeting has ended",
        "Return to home",
        "Rejoin",
    ]
    while True:
        try:
            for t in end_texts:
                try:
                    if await page.locator(f'text="{t}"').count() > 0:
                        print(f"[INFO] End-of-meeting detected: {t}")
                        try:
                            await _emit_state("ended")
                        except Exception:
                            pass
                        try:
                            await page.context.close()
                        except Exception:
                            pass
                        return
                except Exception:
                    continue
        except Exception:
            pass
        await asyncio.sleep(5)

async def _ui_popup_watchdog(page) -> None:
    """Background task to periodically dismiss blocking popups (e.g., 'Got it')."""
    while True:
        try:
            await _dismiss_got_it_popup(page)
        except Exception:
            pass
        await asyncio.sleep(2)


async def _debug_caption_dom(page):
    # Prints where Meet is rendering caption text (helps when layouts change).
    try:
        info = await page.evaluate("""
        () => {
            const take = (sel) => {
                const els = Array.from(document.querySelectorAll(sel));
                const sample = els.slice(0, 3).map(e => (e.textContent || '').trim()).filter(Boolean);
                return { count: els.length, sample };
            };
            return {
                regionCaptions: take('div[role="region"][aria-label*="Captions" i]'),
                regionSubtitles: take('div[role="region"][aria-label*="Subtitles" i]'),
                jsnameYSxPC: take('[jsname="YSxPC"]'),
                blocks: take('.nMcdL'),
                turnOffBtn: take('button[aria-label*="Turn off captions" i]'),
                turnOnBtn: take('button[aria-label*="Turn on captions" i]'),
                turnOffSubsBtn: take('button[aria-label*="Turn off subtitles" i]'),
                turnOnSubsBtn: take('button[aria-label*="Turn on subtitles" i]'),
                jsnameCaptionBtn: take('[jsname="r8qRAd"]'),
            };
        }
        """)
        print("[DOM DEBUG]", info)
    except Exception as e:
        print(f"[DOM DEBUG] failed: {e}")


async def _wait_for_in_call_ui(page, timeout_seconds: int = 600) -> bool:
    deadline = time.time() + timeout_seconds
    selectors = [
        'button[aria-label*="Leave call" i]',
        'button[aria-label*="Leave meeting" i]',
        # Sometimes captions toggle exists before we can find the leave button
        'button[aria-label*="Turn on captions" i]',
        '[role="button"][aria-label*="Turn on captions" i]',
        '[jsname="r8qRAd"][aria-label*="Turn on captions" i]',
    ]

    while time.time() < deadline:
        await _wake_meet_controls(page)

        for sel in selectors:
            try:
                loc = page.locator(sel)
                if await loc.count() > 0 and await loc.first.is_visible():
                    return True
            except Exception:
                continue

        # Common lobby/admission states
        try:
            lobby_texts = [
                "Asking to join",
                "Someone will let you in soon",
                "You can't join this meeting",
                "You can’t join this meeting",
            ]
            for t in lobby_texts:
                if await page.locator(f'text="{t}"').count() > 0:
                    break
        except Exception:
            pass

        await asyncio.sleep(1.0)

    return False

async def _attach_caption_observer(page, meet_link: str):

    parsed = urlparse(meet_link)
    meeting_id = parsed.path.strip('/').split('/')[-1] or 'unknown'

    last_caption_time = {"t": time.time()}

    # Incremental transcript via periodic batching: every N seconds emit only
    # the new text portion per speaker (delta from last emitted).
    emit_interval_seconds = float(os.getenv("CAPTION_EMIT_INTERVAL_SECONDS", "4.0"))
    curr_by_speaker: dict[str, str] = {}
    emitted_last_by_speaker: dict[str, str] = {}

    async def _emit_final(speaker: str, text: str, ts: float):
        txt = (text or '').strip()
        spk = (speaker or '').strip()
        if not txt:
            return

        now = time.time()
        dedupe_key = f"{spk}|{txt}"
        if dedupe_key in LAST_SENT and now - LAST_SENT.get(dedupe_key, 0) < DEDUP_WINDOW_SECONDS:
            return
        LAST_SENT[dedupe_key] = now
        if len(LAST_SENT) > 5000:
            # Keep memory bounded on very long meetings.
            cutoff = now - 120
            for k, v in list(LAST_SENT.items()):
                if v < cutoff:
                    LAST_SENT.pop(k, None)

        prefix = f"{spk}: " if spk else ""
        line = f"[{time.strftime('%H:%M:%S')}] {prefix}{txt}"
        print(line)

        # Prefer backend write; fallback to local file if backend unavailable.
        try:
            payload = {
                "text": txt,
                "speaker": spk,
                "ts": ts,
                "meet_link": meet_link,
                "meeting_id": meeting_id,
                "bot_id": BOT_ID,
            }
            req = urllib.request.Request(
                BACKEND_URL,
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=5) as resp:  # nosec - internal service
                status_code = getattr(resp, "status", None) or resp.getcode()
            if int(status_code) >= 400:
                raise RuntimeError(f"backend status {status_code}")
        except Exception:
            try:
                CAPTIONS_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
                with CAPTIONS_LOG_PATH.open("a", encoding="utf-8") as f:
                    f.write(line + "\n")
            except Exception:
                pass

    # Delta helper: longest common prefix (case-insensitive, collapses whitespace)
    def _prefix_len(a: str, b: str) -> int:
        i = j = 0
        la = len(a)
        lb = len(b)
        while i < la and j < lb:
            ca = a[i]
            cb = b[j]
            if ca.isspace():
                while i < la and a[i].isspace():
                    i += 1
                ca = ' '
            if cb.isspace():
                while j < lb and b[j].isspace():
                    j += 1
                cb = ' '
            if ca.lower() == cb.lower():
                i += 1
                j += 1
                continue
            break
        return j

    async def _on_caption(data):
        curr = (data.get("text") or "").strip()
        speaker = (data.get("speaker") or "").strip()
        if not curr:
            return

        # Avoid speaker flicker when Meet omits the badge briefly.
        if not speaker:
            speaker = "Unknown"

        # Update latest snapshot; emission happens in periodic task
        last_caption_time["t"] = time.time()
        curr_by_speaker[speaker] = curr

    async def _emit_periodic():
        while True:
            await asyncio.sleep(max(0.2, emit_interval_seconds))
            now = time.time()
            for speaker, curr in list(curr_by_speaker.items()):
                prev = emitted_last_by_speaker.get(speaker, "")
                if prev:
                    k = _prefix_len(prev, curr)
                    delta = curr[k:].strip()
                else:
                    delta = curr
                if delta and len(delta) >= 2:
                    await _emit_final(speaker, delta, now)
                    emitted_last_by_speaker[speaker] = curr
                else:
                    # Keep emitted snapshot in sync to avoid repeated prefix
                    emitted_last_by_speaker[speaker] = prev or curr

    await page.expose_function("onCaption", _on_caption)

    # Python-side watchdog so it's obvious when nothing is being captured
    async def _watchdog():
        while True:
            await asyncio.sleep(10)
            idle = time.time() - last_caption_time["t"]
            if idle > 60:
                print(f"[CaptionBot] No captions received for {int(idle)}s")
    asyncio.create_task(_watchdog())
    asyncio.create_task(_emit_periodic())

    def _log_console(msg):
        t_attr = getattr(msg, "type", None)
        t = t_attr() if callable(t_attr) else t_attr
        txt_attr = getattr(msg, "text", None)
        txt = txt_attr() if callable(txt_attr) else txt_attr
        print(f"[page {t}] {txt}")
    page.on("console", _log_console)

    await page.evaluate("""
    (() => {
        console.log("[CaptionBot] Starting caption observer...");

        // Observe ONLY the captions panel. Then read from aria-live nodes inside it.
        // This avoids capturing random UI strings (tooltips/buttons).
        const badgeSel = ".NWpY1d, .xoMHSc";
        const isSystemText = (t) => /turn off captions|turn on captions|closed_caption|leave call|learn more|feedback|you left the meeting|you’ve left the call/i.test(t);
        window.captions_seen = new Set();
        let lastSpeaker = "";

        const findContainer = () => {
            const candidates = [];
            const region = document.querySelector('div[role="region"][aria-label*="Captions" i]');
            if (region) candidates.push(region);
            document.querySelectorAll('[jsname="YSxPC"]').forEach(e => candidates.push(e));

            if (!candidates.length) return null;
            // Prefer the candidate that actually contains speaker badges
            for (const c of candidates) {
                if (c.querySelectorAll(badgeSel).length) return c;
            }
            // Else pick the largest text container
            let best = candidates[0];
            let bestLen = ((best.textContent || '').trim().length);
            for (const c of candidates) {
                const l = ((c.textContent || '').trim().length);
                if (l > bestLen) {
                    best = c;
                    bestLen = l;
                }
            }
            return best;
        };

        const emitLine = (speaker, line) => {
            const s = (speaker || "").trim();
            const t = (line || "").trim();
            if (!t || t.length < 2) return;
            if (isSystemText(t)) return;
            const key = `${s}|${t}`;
            if (window.captions_seen.has(key)) return;
            window.captions_seen.add(key);
            if (window.captions_seen.size > 2000) {
                // Prevent long-meeting memory growth.
                window.captions_seen.clear();
            }
            // Mark last caption timestamp so Python can avoid leaving while speech is ongoing.
            try { window.lastCaptionTs = Date.now(); } catch(e) {}
            try { window.onCaption({ text: t, speaker: s, ts: Date.now()/1000 }); } catch(e) {}
        };

        const scan = (container) => {
            // Primary strategy (matches your DOM):
            // <div class="nMcdL ...">
            //   <span class="NWpY1d">Speaker</span>
            //   <div class="ygicle VbkSUe">Caption text</div>
            // </div>
            const blockSel = '.nMcdL';
            const speakerSel = '.NWpY1d, .xoMHSc';
            const captionSel = '.ygicle.VbkSUe, .ygicle';

            const blocks = Array.from(container.querySelectorAll(blockSel));
            if (blocks.length) {
                const recent = blocks.slice(-6);
                for (const block of recent) {
                    const speakerEl = block.querySelector(speakerSel);
                    const speaker = (speakerEl?.textContent || '').trim() || lastSpeaker;
                    if (speaker) lastSpeaker = speaker;

                    const capEl = block.querySelector(captionSel);
                    const raw = (capEl?.innerText || capEl?.textContent || '').trim();
                    if (!raw) continue;
                    if (speaker && raw.toLowerCase() === speaker.toLowerCase()) continue;
                    if (isSystemText(raw)) continue;

                    raw.split(/\\n+/).map(s => s.trim()).filter(Boolean).forEach(line => {
                        if (speaker && line.toLowerCase() === speaker.toLowerCase()) return;
                        emitLine(speaker, line);
                    });
                }
                return;
            }

            // Fallback strategy: aria-live nodes within the captions container (some layouts)
            const lives = Array.from(container.querySelectorAll('[aria-live]'));
            for (const live of lives) {
                const text = (live.textContent || "").trim();
                if (!text) continue;
                const badge = live.querySelector?.(badgeSel);
                const speaker = (badge?.textContent || "").trim() || lastSpeaker;
                if (speaker) lastSpeaker = speaker;
                if (isSystemText(text)) continue;
                text.split(/\\n+/).map(s => s.trim()).filter(Boolean).forEach(line => {
                    if (speaker && line.toLowerCase() === speaker.toLowerCase()) return;
                    emitLine(speaker, line);
                });
            }
        };

        const attach = (container) => {
            console.log("[CaptionBot] Captions container found; attaching observer");
            const observer = new MutationObserver(() => scan(container));
            observer.observe(container, { childList: true, subtree: true, characterData: true });
            // periodic scan as a safety net
            setInterval(() => scan(container), 800);
            scan(container);
        };

        const poll = setInterval(() => {
            const c = findContainer();
            if (c) {
                clearInterval(poll);
                attach(c);
            } else {
                console.log("[CaptionBot] Waiting for captions container... (CC must be ON)");
            }
        }, 1000);

        // Debug stats every 5s so we can see if captions DOM exists
        setInterval(() => {
            const c = findContainer();
            const tag = c ? (c.tagName || 'unknown') : 'none';
            const lives = c ? c.querySelectorAll('[aria-live]').length : 0;
            const badgeCount = c ? c.querySelectorAll(badgeSel).length : 0;
            const blockCount = c ? c.querySelectorAll('.nMcdL').length : 0;
            const caps = c ? Array.from(c.querySelectorAll('.ygicle.VbkSUe')) : [];
            const lastCapEl = caps.length ? caps[caps.length - 1] : null;
            const lastCap = lastCapEl ? ((lastCapEl.innerText || lastCapEl.textContent || '').trim().slice(0, 60)) : '';
            const sample = c ? (c.textContent || '').trim().slice(0, 80) : '';
            console.log(`[CaptionBot][stats] container=${tag} ariaLive=${lives} badges=${badgeCount} blocks=${blockCount} lastCapLen=${lastCap.length} sampleLen=${sample.length}`);
        }, 5000);
    })();
    """)

async def main():
    async with async_playwright() as p:
        # Headless mode can be enabled via env: HEADLESS=1
        browser = await p.chromium.launch(headless=HEADLESS)
        
        # Load signed-in session from auth.json
        storage_state_path = Path(__file__).resolve().parent / "auth.json"
        if storage_state_path.exists():
            print(f"Using storage state: {storage_state_path}")
            context = await browser.new_context(storage_state=str(storage_state_path))
        else:
            context = await browser.new_context()
        
        page = await context.new_page()
        await page.goto(MEET_LINK, wait_until="domcontentloaded")
        await page.wait_for_load_state("networkidle")
        print("Navigated to Google Meet...")

        # Turn off mic & camera
        try:
            # Common prompt texts; try a few variants
            for sel in [
                'text="Continue without microphone and camera"',
                'text="Continue without mic and camera"',
                'button:has-text("Continue")',
            ]:
                if await page.locator(sel).count() > 0:
                    await page.locator(sel).first.click()
                    print("Clicked: Continue (mic/camera off)")
                    break
        except Exception:
            print("Mic/camera prompt not found or already skipped")

        # Signed-in flow: skip guest name fill

        # Helper: robust join button click
        async def click_join_button() -> bool:
            selectors = [
                # Text-based
                'button:has-text("Ask to join")',
                'button:has-text("Join now")',
                'div[role="button"]:has-text("Ask to join")',
                'div[role="button"]:has-text("Join now")',
                # ARIA label variants
                'button[aria-label*="Join" i]',
                'button[aria-label*="Ask" i]',
            ]
            for s in selectors:
                try:
                    loc = page.locator(s)
                    if await loc.count() > 0:
                        await loc.first.scroll_into_view_if_needed()
                        await loc.first.click()
                        print(f"Clicked join using selector: {s}")
                        return True
                except Exception:
                    continue
            # Role-based API
            try:
                await page.get_by_role("button", name="Ask to join").click()
                print("Clicked join via role: Ask to join")
                return True
            except Exception:
                pass
            try:
                await page.get_by_role("button", name="Join now").click()
                print("Clicked join via role: Join now")
                return True
            except Exception:
                pass
            # Fallback: keyboard (Tab to focus + Enter)
            try:
                await page.keyboard.press("Tab")
                await page.keyboard.press("Enter")
                print("Attempted join via keyboard fallback")
                return True
            except Exception:
                pass
            return False

        # Automatically click "Ask to join" / "Join now"
        try:
            clicked = await click_join_button()
            if not clicked:
                # Try waiting briefly and re-checking after potential UI changes
                await asyncio.sleep(2)
                clicked = await click_join_button()
            if clicked:
                print("[OK] Clicked: Ask to join / Join now")
            else:
                print("Join button not found; UI may require host admission or different layout")
        except PlaywrightTimeoutError:
            print("Join action timed out")

        # Wait until we're actually in the call UI before trying captions.
        # If we're in the lobby ("Ask to join" flow), captions controls are not available.
        in_call = await _wait_for_in_call_ui(page, timeout_seconds=600)
        if in_call:
            print("[OK] In-call UI detected")
        else:
            print("[WARN] In-call UI not detected within timeout (may be waiting for admission)")

        # Give Meet UI a moment to stabilize after join
        await asyncio.sleep(2)

        # Start a popup watchdog to auto-dismiss blocking dialogs (e.g., 'Got it').
        try:
            asyncio.create_task(_ui_popup_watchdog(page))
        except Exception:
            pass

        # Try to enable captions (best effort); don't block chat on this outcome.
        enabled = await _ensure_captions_on(page)
        print(f"Captions enabled attempt result: {enabled}")

        # If not enabled yet, keep trying in the background while proceeding with chat.
        async def _keep_captions_on():
            
    # Only retry captions until they are ON once
            for _ in range(10):  # try for ~2.5 minutes max
                try:
                    await asyncio.sleep(15)
                    if await _captions_on(page):
                        print("[BG] Captions confirmed ON. Stopping keeper.")
                        return
                    await _ensure_captions_on(page)
                except Exception:
                    continue

        asyncio.create_task(_keep_captions_on())

        # Open chat and send message regardless of captions state (once in-call).
        try:
            opened = await _open_chat_panel(page)
            print(f"Chat panel open attempt result: {opened}")
            if not opened:
                await asyncio.sleep(1.5)
                opened = await _open_chat_panel(page)
                print(f"Chat panel second attempt result: {opened}")
            if opened and CHAT_ON_JOIN:
                try:
                    sent = await _send_chat_message(page, CHAT_ON_JOIN)
                    if not sent:
                        print("[WARN] Failed to send CHAT_ON_JOIN message")
                except Exception:
                    print("[WARN] Failed to send CHAT_ON_JOIN message")
            elif opened and not CHAT_ON_JOIN:
                print("[INFO] CHAT_ON_JOIN is empty; skipping chat send")
        except Exception:
            print("[WARN] Chat panel open failed")

        try:
            await _emit_state("running")
        except Exception:
            pass

        # One-time DOM snapshot to confirm where captions are rendering
        await _debug_caption_dom(page)

        # Attach observer (polls for captions container)
        await _attach_caption_observer(page, MEET_LINK)
        print("[OK] Captions observer attached. Waiting...")

        # Listen for backend commands (e.g., send Meet chat messages).
        asyncio.create_task(_command_poll_loop(page))
        # Prepare participant tracking via injected JS and open People panel once.
        try:
            await _inject_user_manager(page)
        except Exception:
            pass
        try:
            await _open_people_panel(page)
        except Exception:
            pass
        # Leave automatically if alone for a sustained period.
        asyncio.create_task(_monitor_alone_and_leave(page))
        # Also detect explicit end-of-meeting screens and finalize.
        asyncio.create_task(_monitor_meeting_end(page))
        await asyncio.sleep(3600)  # Keep browser open

asyncio.run(main())