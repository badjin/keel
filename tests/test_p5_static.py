"""Task 5.4: static assets for the Korean installer UI.

Checks structural requirements from DOCS/plans/2026-09-23-starter-kit-plan.md
Task 5.4 step 1: files exist, required Korean labels present, no stray
network references, token header used, palette/dark-mode present, and the
"no generic AI aesthetics" bans (no gradients, no backdrop-filter).
"""
import re
import subprocess
import unittest
from pathlib import Path

STATIC = Path(__file__).resolve().parent.parent / "app" / "static"
ROOT = STATIC.parent.parent


class TestStaticFilesExist(unittest.TestCase):
    def test_files_exist(self):
        for name in ("index.html", "app.css", "app.js"):
            path = STATIC / name
            self.assertTrue(path.is_file(), f"missing {path}")


class TestAppJsIsTracked(unittest.TestCase):
    def test_app_js_is_git_tracked(self):
        # ~/.gitignore_global has a stray rule matching literal "app.js" that
        # is unrelated to this repo; the repo .gitignore negates it
        # (`!app/static/app.js`) so the file stays trackable. Guard against
        # that negation regressing.
        if not (ROOT / ".git").exists():
            self.skipTest("not a git work tree")
        result = subprocess.run(
            ["git", "ls-files", "app/static/app.js"],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.stdout.strip(), "app/static/app.js")


class TestIndexHtml(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = (STATIC / "index.html").read_text(encoding="utf-8")

    def test_no_stray_network_urls(self):
        urls = re.findall(r"https?://\S+", self.html)
        self.assertEqual(urls, [], f"unexpected network URLs in index.html: {urls}")


class TestAppJs(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.js = (STATIC / "app.js").read_text(encoding="utf-8")

    def test_uses_token_header(self):
        self.assertIn("X-Kit-Token", self.js)

    def test_shows_gh_login_button_from_env(self):
        self.assertIn("gh_cli", self.js)
        self.assertIn('"btn-gh-login"', self.js)
        self.assertIn("btn-gh-login\").hidden = !state.env.gh_cli", self.js)

    def test_only_allowed_network_url(self):
        # The SVG XML namespace URI is a technical identifier required by
        # document.createElementNS, never fetched over the network.
        allowed_prefixes = ("https://obsidian.md/download", "http://www.w3.org/2000/svg")
        urls = re.findall(r'https?://[^\s"\'`)]+', self.js)
        for url in urls:
            self.assertTrue(
                url.startswith(allowed_prefixes),
                f"unexpected network URL in app.js: {url}",
            )

    def test_finish_summary_groups_hook_ids_by_cli(self):
        self.assertIn("Object.entries(result.hooks || {})", self.js)


class TestStaticNetworkReferences(unittest.TestCase):
    def test_only_obsidian_and_svg_namespace_urls_are_present(self):
        allowed = {"https://obsidian.md/download", "http://www.w3.org/2000/svg"}
        for name in ("index.html", "app.css", "app.js"):
            source = (STATIC / name).read_text(encoding="utf-8")
            urls = re.findall(r'https?://[^\s"\'`)]+', source)
            for url in urls:
                self.assertIn(url, allowed, f"unexpected network URL in {name}: {url}")


class TestAppCss(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.css = (STATIC / "app.css").read_text(encoding="utf-8")

    def test_no_stray_network_urls(self):
        urls = re.findall(r"https?://\S+", self.css)
        self.assertEqual(urls, [], f"unexpected network URLs in app.css: {urls}")

    def test_defines_docs_theme_color_tokens(self):
        root = re.search(r":root\s*\{(.*?)\}", self.css, re.S)
        self.assertIsNotNone(root, "no :root token block found")
        expected = {
            "background": "#ffffff", "subtle": "#fafafa", "muted": "#f4f4f5",
            "border": "#e4e4e7", "border-strong": "#d4d4d8", "text": "#09090b", "muted-text": "#71717a",
            "primary": "#18181b", "primary-text": "#fafafa", "accent": "#2563eb",
            "success": "#16a34a", "danger": "#dc2626",
        }
        for name, value in expected.items():
            self.assertRegex(root.group(1), rf"--{name}:\s*{re.escape(value)}")

    def test_has_no_dark_mode_block(self):
        self.assertNotIn("prefers-color-scheme: dark", self.css)

    def test_all_border_widths_are_one_or_zero_pixels(self):
        declarations = re.findall(r"\b(border(?:-(?:top|right|bottom|left))?)\s*:\s*([^;}]+)", self.css)
        self.assertTrue(declarations, "expected border declarations")
        for prop, value in declarations:
            widths = re.findall(r"(?<![\w.-])(\d+)px\b", value)
            self.assertTrue(
                all(width in {"0", "1"} for width in widths),
                f"{prop} has non-hairline width: {value}",
            )

    def test_border_declarations_do_not_use_text_token(self):
        declarations = re.findall(r"\bborder(?:-[a-z]+)?\s*:\s*([^;}]+)", self.css)
        self.assertTrue(declarations, "expected border declarations")
        for value in declarations:
            self.assertNotIn("var(--text)", value)

    def test_selected_cards_use_strong_border_without_inner_highlight(self):
        for selector in (".card.selected", ".choice-card:has(input:checked)"):
            match = re.search(rf"{re.escape(selector)}\s*\{{([^}}]*)\}}", self.css)
            self.assertIsNotNone(match, f"missing {selector} rule")
            self.assertIn("border-color: var(--border-strong)", match.group(1))
            self.assertNotIn("box-shadow", match.group(1))

    def test_cards_have_symmetric_padding_and_no_last_child_bottom_margin(self):
        self.assertRegex(self.css, r"\.card\s*\{[^}]*padding:\s*16px 20px")
        self.assertRegex(
            self.css, r"\.card > :last-child\s*\{\s*margin-bottom:\s*0;\s*\}"
        )

    def test_last_paragraph_in_a_card_has_no_bottom_margin(self):
        # `.card p.desc` and `.card p.example, .field-note` both set an
        # explicit margin-bottom with specificity equal to `.card >
        # :last-child`, so that reset alone does not win the cascade for a
        # <p> — a dedicated `.card p:last-child` rule is required (Task 6.2
        # step 6b).
        self.assertRegex(
            self.css, r"\.card p:last-child\s*\{\s*margin-bottom:\s*0;\s*\}"
        )

    def test_no_gradients(self):
        self.assertNotIn("linear-gradient(", self.css)

    def test_no_backdrop_filter(self):
        self.assertNotIn("backdrop-filter", self.css)

    def test_old_catalogue_style_is_absent(self):
        for banned in ("#F4EFE6", "#FBF8F2", "AppleMyungjo", "Nanum Myeongjo"):
            self.assertNotIn(banned, self.css)

    def test_tab_active_state_has_no_translate(self):
        # Task 5.6: the tab column must not shift when a tab becomes active —
        # active state is background + left-rule color only.
        blocks = re.findall(
            r'\.tab\[aria-selected="true"\]\s*\{[^}]*\}', self.css
        )
        self.assertTrue(blocks, "no .tab[aria-selected=\"true\"] rule found")
        for block in blocks:
            self.assertNotIn("translate", block)
            self.assertNotIn("width", block)

    def test_tab_column_width_is_fixed_not_overridden_on_active(self):
        self.assertIn("grid-template-columns: 248px", self.css)
        self.assertNotIn("width: 220px", self.css)

    def test_docs_layout_landmarks_present(self):
        html = (STATIC / "index.html").read_text(encoding="utf-8")
        self.assertIn('class="masthead"', html)


class TestLifecycleRailMarkup(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = (STATIC / "index.html").read_text(encoding="utf-8")
        cls.js = (STATIC / "app.js").read_text(encoding="utf-8")

    def test_rail_container_present(self):
        self.assertIn('id="lifecycle-rail"', self.html)

    def test_rail_renders_one_station_per_event_with_count_badge(self):
        # Stations are rendered client-side (one per STATIONS entry); the
        # renderer must attach a count badge slot per station.
        self.assertIn("rail-station", self.js)
        self.assertIn("dataset.count", self.js)
        self.assertIn('"rail-count"', self.js)


class TestHotfixAppJs(unittest.TestCase):
    """Task 5.8: UI state and error display hotfix."""

    @classmethod
    def setUpClass(cls):
        cls.js = (STATIC / "app.js").read_text(encoding="utf-8")
        cls.html = (STATIC / "index.html").read_text(encoding="utf-8")

    def test_selected_hooks_is_rebuilt_inside_render_hook_cards(self):
        match = re.search(r"function renderHookCards\(\)\s*\{(.*?)\n\}\n", self.js, re.S)
        self.assertIsNotNone(match, "renderHookCards() not found")
        body = match.group(1)
        self.assertIn("state.selectedHooks =", body)

    def test_repo_list_render_sets_checked_from_selected_repos(self):
        match = re.search(r"function renderRepoList\(\)\s*\{(.*?)\n\}\n", self.js, re.S)
        self.assertIsNotNone(match, "renderRepoList() not found")
        body = match.group(1)
        self.assertIn("state.selectedRepos", body)
        self.assertIn("box.checked", body)

    def test_period_number_input_clamps_on_change_not_on_input(self):
        input_match = re.search(r'dueNumber\.addEventListener\("input",\s*\(\)\s*=>\s*(\w+)\(', self.js)
        change_match = re.search(r'dueNumber\.addEventListener\("change",\s*\(\)\s*=>\s*(\w+)\(', self.js)
        self.assertIsNotNone(input_match, "dueNumber input listener not found")
        self.assertIsNotNone(change_match, "dueNumber change listener not found")
        self.assertEqual(change_match.group(1), "syncDue")
        self.assertNotEqual(input_match.group(1), "syncDue")

    def test_ongithubconnected_calls_are_always_awaited(self):
        calls = [
            m for m in re.finditer(r"(await\s+)?onGithubConnected\(", self.js)
            if not self.js[: m.start()].rstrip().endswith(("function", "async"))
        ]
        # Exclude the function declaration itself.
        calls = [m for m in calls if "function onGithubConnected(" not in self.js[max(0, m.start() - 15) : m.end() + 5]]
        self.assertTrue(calls, "no onGithubConnected( call sites found")
        for m in calls:
            self.assertTrue(m.group(1), f"onGithubConnected( call not preceded by await: {self.js[max(0, m.start()-20):m.end()]!r}")

    def test_finish_error_exists_and_is_used_by_finish_and_obsidian_handlers(self):
        self.assertIn('id="finish-error"', self.html)
        for handler in ("btn-finish", "btn-obsidian-open", "btn-obsidian-recheck"):
            idx = self.js.index(f'"{handler}"')
            snippet = self.js[idx : idx + 300]
            self.assertIn("finish-error", snippet, f"{handler} handler does not reference #finish-error")

    def test_scroll_into_view_respects_prefers_reduced_motion(self):
        idx = self.js.index("scrollIntoView")
        preceding = self.js[max(0, idx - 400) : idx]
        self.assertIn("prefers-reduced-motion", preceding)


class TestHotfixAppCss(unittest.TestCase):
    """Task 5.8: unused/leftover CSS cleaned up."""

    @classmethod
    def setUpClass(cls):
        cls.css = (STATIC / "app.css").read_text(encoding="utf-8")

    def test_no_card_disabled_selector(self):
        self.assertNotIn(".card.disabled", self.css)

    def test_no_mono_class_selector(self):
        self.assertNotIn(".mono", self.css)

    def test_no_btn_disabled_transform_none(self):
        match = re.search(r"\.btn:disabled\s*\{[^}]*\}", self.css)
        self.assertIsNotNone(match)
        self.assertNotIn("transform", match.group(0))

    def test_no_leftover_transform_transitions(self):
        self.assertNotRegex(self.css, r"(?:^|[;{}\s])transform\s*:")

    def test_btn_block_is_not_duplicated(self):
        blocks = re.findall(r"(?m)^\.btn\s*\{", self.css)
        self.assertEqual(len(blocks), 1, f"expected exactly one top-level .btn {{ block, found {len(blocks)}")


class TestHotfixWikiLocalGitRepos(unittest.TestCase):
    """Task 5.8: local repo list on the GitHub tab must come from the
    git_repos the server returns, not from parsing rendered folder rows."""

    @classmethod
    def setUpClass(cls):
        cls.js = (STATIC / "app.js").read_text(encoding="utf-8")

    def test_git_subfolders_state_comes_from_api_response(self):
        self.assertIn("localResult.git_repos", self.js)
        self.assertNotIn('.textContent.includes("(git)")', self.js)


class TestStepNumDesign(unittest.TestCase):
    """Task 6.2 step 6b: step headings render the number as a muted
    `.step-num` span instead of plain text."""

    def test_step_num_style_defined(self):
        css = (STATIC / "app.css").read_text(encoding="utf-8")
        match = re.search(r"\.step-num\s*\{([^}]*)\}", css)
        self.assertIsNotNone(match, ".step-num rule not found in app.css")
        self.assertIn("color: var(--muted-text)", match.group(1))
        self.assertIn("margin-right: 8px", match.group(1))

    def test_step_num_present_for_all_four_headings(self):
        html = (STATIC / "index.html").read_text(encoding="utf-8")
        self.assertEqual(html.count('class="step-num"'), 4)


class TestUninstallRemoveSkillsControl(unittest.TestCase):
    """Task 6.2 step 7: the 훅 tab's uninstall button gets a 스킬도 제거
    checkbox sent as remove_skills."""

    def test_checkbox_present_in_hooks_panel(self):
        html = (STATIC / "index.html").read_text(encoding="utf-8")
        panel = re.search(r'<section class="panel" id="panel-hooks".*?</section>', html, re.S)
        self.assertIsNotNone(panel)
        self.assertIn('id="uninstall-remove-skills"', panel.group(0))
        self.assertIn("hooks.removeSkillsToo", panel.group(0))

    def test_uninstall_handler_sends_remove_skills(self):
        js = (STATIC / "app.js").read_text(encoding="utf-8")
        idx = js.index('"btn-uninstall-hooks"')
        snippet = js[idx : idx + 500]
        self.assertIn("uninstall-remove-skills", snippet)
        self.assertIn("remove_skills", snippet)


class TestHotfixLoadFolderListErrorHandling(unittest.TestCase):
    """Task 6.3 step 6: every loadFolderList( call site is awaited inside
    try/catch or followed by .catch( — routed to the wiki panel's inline
    error (#wiki-error)."""

    @classmethod
    def setUpClass(cls):
        cls.js = (STATIC / "app.js").read_text(encoding="utf-8")

    def test_every_call_site_is_try_catch_or_dot_catch(self):
        calls = [
            m for m in re.finditer(r"loadFolderList\(", self.js)
            if "async function loadFolderList(" not in self.js[max(0, m.start() - 15) : m.end() + 5]
        ]
        self.assertTrue(calls, "no loadFolderList( call sites found")
        for m in calls:
            tail = self.js[m.end() : m.end() + 200]
            preceding = self.js[max(0, m.start() - 20) : m.start()]
            self.assertTrue(
                ".catch(" in tail or "await" in preceding,
                f"loadFolderList( call not guarded by await/try-catch or .catch(: {self.js[max(0, m.start()-20):m.end()+20]!r}",
            )

    def test_failures_route_to_wiki_error(self):
        self.assertIn('showError("wiki-error"', self.js)


class TestHotfixDeadCodeRemoved(unittest.TestCase):
    """Task 6.3 step 6: hidden .stamp element, unused state.completedSteps,
    and unused chosenFolders[].name are gone."""

    @classmethod
    def setUpClass(cls):
        cls.js = (STATIC / "app.js").read_text(encoding="utf-8")
        cls.css = (STATIC / "app.css").read_text(encoding="utf-8")

    def test_no_stamp_element_created(self):
        self.assertNotIn('className = "stamp"', self.js)

    def test_no_completed_steps_state(self):
        self.assertNotIn("completedSteps", self.js)

    def test_check_marks_still_work_via_is_complete_class(self):
        self.assertIn('tab.classList.add("is-complete")', self.js)

    def test_chosen_folders_push_has_no_name_field(self):
        self.assertIn("state.chosenFolders.push({ path: dirPath, isGit });", self.js)

    def test_stamp_selectors_removed_from_css(self):
        self.assertNotIn(".stamp", self.css)


class TestHotfixScrollMarginAndBadgePadding(unittest.TestCase):
    """Task 6.3 step 6: scroll-margin-top on the right-column anchor
    targets, and extra card padding so the ✓ badge never overlaps a
    title."""

    ANCHORS = [
        "#hook-targets", "#lifecycle-rail", "#event-SessionStart",
        "#event-UserPromptSubmit", "#event-PreToolUse", "#event-PostToolUse",
        "#event-Stop", "#event-SessionEnd", "#wiki-structure",
        "#wiki-path-section", "#wiki-mode-cards", "#github-connect",
        "#repo-list", "#github-period", "#github-run",
        "#finish-summary-section", "#obsidian-section",
    ]

    @classmethod
    def setUpClass(cls):
        cls.css = (STATIC / "app.css").read_text(encoding="utf-8")

    def test_anchor_targets_have_72px_scroll_margin(self):
        match = re.search(r"([^{}]+)\{\s*scroll-margin-top:\s*72px;?\s*\}", self.css)
        self.assertIsNotNone(match, "no 72px scroll-margin-top rule found")
        selectors = match.group(1)
        for anchor in self.ANCHORS:
            self.assertIn(anchor, selectors, f"{anchor} missing from 72px scroll-margin rule")

    def test_anchor_targets_have_124px_scroll_margin_under_768(self):
        narrow = re.search(r"@media \(max-width: 767px\)\s*\{(.*?)\n@media \(max-width: 420px\)", self.css, re.S)
        self.assertIsNotNone(narrow, "narrow media query block not found")
        match = re.search(r"([^{}]+)\{\s*scroll-margin-top:\s*124px;?\s*\}", narrow.group(1))
        self.assertIsNotNone(match, "no 124px scroll-margin-top rule found under 768px")
        selectors = match.group(1)
        for anchor in self.ANCHORS:
            self.assertIn(anchor, selectors, f"{anchor} missing from 124px scroll-margin rule")

    def test_selected_card_and_choice_card_get_extra_right_padding_for_badge(self):
        self.assertRegex(self.css, r"\.card\.selected\s*\{\s*padding-right:\s*44px;?\s*\}")
        self.assertRegex(
            self.css,
            r"\.choice-card:has\(input:checked\)\s*\{\s*padding-right:\s*44px;?\s*\}",
        )


class TestHotfixTask64Static(unittest.TestCase):
    """Task 6.4 step 6: loadFolderListSafe clears the wiki error on
    success, and the finish-panel undo steps mention ticking both CLI
    checkboxes plus that `hooks = true` stays."""

    @classmethod
    def setUpClass(cls):
        cls.js = (STATIC / "app.js").read_text(encoding="utf-8")

    def test_load_folder_list_safe_clears_wiki_error_on_success(self):
        match = re.search(r"function loadFolderListSafe\(path\)\s*\{(.*?)\n\}\n", self.js, re.S)
        self.assertIsNotNone(match, "loadFolderListSafe not found")
        self.assertIn('clearError("wiki-error")', match.group(1))


class TestClickableRowLabels(unittest.TestCase):
    """Task 6.5 step 1: clicking a row's label text toggles its checkbox in
    #repo-list and #local-repo-list; #folder-browser rows keep the existing
    click-to-navigate behavior on the name instead."""

    @classmethod
    def setUpClass(cls):
        cls.js = (STATIC / "app.js").read_text(encoding="utf-8")

    def test_repo_list_label_nests_the_checkbox(self):
        match = re.search(r"function renderRepoList\(\)\s*\{(.*?)\n\}\n", self.js, re.S)
        self.assertIsNotNone(match, "renderRepoList() not found")
        body = match.group(1)
        self.assertRegex(body, r"label\.appendChild\(\s*box\s*\)")

    def test_local_repo_list_label_nests_the_checkbox(self):
        match = re.search(r"function renderLocalRepoList\(\)\s*\{(.*?)\n\}\n", self.js, re.S)
        self.assertIsNotNone(match, "renderLocalRepoList() not found")
        body = match.group(1)
        self.assertRegex(body, r"label\.appendChild\(\s*box\s*\)")

    def test_folder_browser_row_associates_label_via_id_for_and_keeps_navigation(self):
        match = re.search(r"function loadFolderList\(path\)\s*\{(.*?)\n\}\n", self.js, re.S)
        self.assertIsNotNone(match, "loadFolderList not found")
        body = match.group(1)
        self.assertRegex(body, r"box\.id\s*=")
        self.assertRegex(body, r"nm\.htmlFor\s*=\s*box\.id")
        self.assertIn("e.preventDefault()", body)
        self.assertIn("loadFolderListSafe(dirPath)", body)


class TestWikiLocationPicker(unittest.TestCase):
    """Task 6.13 step 1: the wiki tab replaces the free-text path field with
    a folder browser (reusing /api/fs/list) for the location plus a separate
    folder-name input; the full path is computed as <location>/<name>."""

    @classmethod
    def setUpClass(cls):
        cls.html = (STATIC / "index.html").read_text(encoding="utf-8")
        cls.js = (STATIC / "app.js").read_text(encoding="utf-8")

    def test_location_browser_markup_present(self):
        self.assertIn('id="wiki-location-browser"', self.html)
        self.assertIn('id="wiki-location-list"', self.html)
        self.assertIn('id="btn-wiki-location-up"', self.html)

    def test_folder_name_input_defaults_to_llm_wiki(self):
        match = re.search(r'<input[^>]*id="wiki-folder-name"[^>]*>', self.html)
        self.assertIsNotNone(match, "wiki-folder-name input not found")
        self.assertIn('value="knowledge-base"', match.group(0))

    def test_wiki_path_field_is_readonly(self):
        match = re.search(r'<input[^>]*id="wiki-path"[^>]*>', self.html)
        self.assertIsNotNone(match, "wiki-path input not found")
        self.assertIn("readonly", match.group(0))

    def test_location_list_uses_fs_list_api(self):
        match = re.search(r"function loadWikiLocationList\(path\)\s*\{(.*?)\n\}\n", self.js, re.S)
        self.assertIsNotNone(match, "loadWikiLocationList not found")
        self.assertIn("/api/fs/list", match.group(1))

    def test_location_list_supports_navigating_up(self):
        match = re.search(r"function loadWikiLocationList\(path\)\s*\{(.*?)\n\}\n", self.js, re.S)
        self.assertIsNotNone(match, "loadWikiLocationList not found")
        self.assertIn("data.parent", match.group(1))

    def test_path_preview_combines_location_and_name(self):
        match = re.search(r"function updateWikiPathPreview\(\)\s*\{(.*?)\n\}\n", self.js, re.S)
        self.assertIsNotNone(match, "updateWikiPathPreview not found")
        body = match.group(1)
        self.assertIn("state.wikiLocation", body)
        self.assertIn("state.wikiFolderName", body)

    def test_init_wiki_sends_computed_wiki_path(self):
        idx = self.js.index('"btn-init-wiki"')
        snippet = self.js[idx : idx + 700]
        self.assertIn("state.wikiPath", snippet)
        self.assertIn("validateWikiFolderName", snippet)

    def test_folder_name_validation_rejects_slash_dotdot_and_blank(self):
        match = re.search(r"function validateWikiFolderName\(name\)\s*\{(.*?)\n\}\n", self.js, re.S)
        self.assertIsNotNone(match, "validateWikiFolderName not found")
        body = match.group(1)
        self.assertIn('"/"', body)
        self.assertIn('".."', body)
        self.assertIn("trim()", body)
        self.assertIn('t("wiki.folderNameRequired")', body)
        self.assertIn('t("wiki.folderNameInvalidChars")', body)

    def test_location_list_loaded_on_boot(self):
        self.assertIn("loadWikiLocationListSafe(state.env.home)", self.js)


def _load_kit_i18n():
    """Parses window.KIT_I18N = {...}; out of i18n.js with json.loads —
    the file is strict JSON on the right-hand side of that assignment."""
    import json

    src = (STATIC / "i18n.js").read_text(encoding="utf-8")
    start = src.index("window.KIT_I18N = ") + len("window.KIT_I18N = ")
    end = src.rindex("};") + 1
    return json.loads(src[start:end])


def _strip_js_comments_and_strings_free_text(js: str) -> str:
    """Best-effort removal of /*...*/ and //... comments, so a Hangul
    scan of app.js does not trip on comment text."""
    js = re.sub(r"/\*.*?\*/", "", js, flags=re.S)
    js = re.sub(r"(?m)//.*$", "", js)
    return js


class TestI18nTable(unittest.TestCase):
    """Task 6.15 step 1: one bilingual message table with identical en/ko
    key sets, used by index.html/app.js instead of inline Korean."""

    def test_i18n_js_exists(self):
        self.assertTrue((STATIC / "i18n.js").is_file())

    def test_en_and_ko_key_sets_match(self):
        table = _load_kit_i18n()
        self.assertIn("en", table)
        self.assertIn("ko", table)
        self.assertEqual(set(table["en"].keys()), set(table["ko"].keys()))
        self.assertTrue(len(table["en"]) > 20)

    def test_no_hangul_in_index_html_outside_i18n_js(self):
        html = (STATIC / "index.html").read_text(encoding="utf-8")
        # The only Korean text allowed directly in the page markup is the
        # toggle's own "한국어" label — a proper noun naming the language,
        # not a translatable UI string.
        stripped = html.replace("한국어", "")
        stripped = re.sub(r"<!--.*?-->", "", stripped, flags=re.S)
        hangul = [ch for ch in stripped if "가" <= ch <= "힣"]
        self.assertEqual(hangul, [], f"unexpected Hangul in index.html: {hangul!r}")

    def test_no_hangul_in_app_js_outside_i18n_js(self):
        js = (STATIC / "app.js").read_text(encoding="utf-8")
        # "한국어" is the language's own proper name (used as a segmented-
        # control option value, same as "English"), not a translatable
        # UI string — same whitelist as the index.html check above.
        stripped = js.replace("한국어", "")
        stripped = _strip_js_comments_and_strings_free_text(stripped)
        hangul = [ch for ch in stripped if "가" <= ch <= "힣"]
        self.assertEqual(hangul, [], f"unexpected Hangul in app.js: {hangul!r}")

    def test_html_has_toggle_with_aria_pressed_and_default_english(self):
        html = (STATIC / "index.html").read_text(encoding="utf-8")
        self.assertIn('id="lang-btn-en"', html)
        self.assertIn('id="lang-btn-ko"', html)
        en_btn = re.search(r'<button[^>]*id="lang-btn-en"[^>]*>', html).group(0)
        ko_btn = re.search(r'<button[^>]*id="lang-btn-ko"[^>]*>', html).group(0)
        self.assertIn('aria-pressed="true"', en_btn)
        self.assertIn('aria-pressed="false"', ko_btn)
        self.assertEqual(html.count('<html lang='), 1)
        self.assertIn('<html lang="en">', html)

    def test_default_language_is_english_when_nothing_stored(self):
        js = (STATIC / "app.js").read_text(encoding="utf-8")
        self.assertIn('let LANG = loadStoredLang() || "en";', js)

    def test_localstorage_access_is_wrapped_in_try_catch(self):
        js = (STATIC / "app.js").read_text(encoding="utf-8")
        get_match = re.search(r"function loadStoredLang\(\)\s*\{(.*?)\n\}\n", js, re.S)
        self.assertIsNotNone(get_match)
        self.assertIn("try {", get_match.group(1))
        self.assertIn("catch", get_match.group(1))
        set_match = re.search(r"function storeLang\(lang\)\s*\{(.*?)\n\}\n", js, re.S)
        self.assertIsNotNone(set_match)
        self.assertIn("try {", set_match.group(1))
        self.assertIn("catch", set_match.group(1))

    def test_switching_language_rerenders_without_reload_or_losing_state(self):
        js = (STATIC / "app.js").read_text(encoding="utf-8")
        match = re.search(r"function setLang\(lang\)\s*\{(.*?)\n\}\n", js, re.S)
        self.assertIsNotNone(match, "setLang not found")
        body = match.group(1)
        self.assertIn("applyI18n()", body)
        self.assertIn("rerenderForLanguage()", body)
        self.assertNotIn("location.reload", js)
        # rerenderForLanguage must read from `state`, not from the DOM,
        # so current selections survive a language switch.
        rerender = re.search(r"function rerenderForLanguage\(\)\s*\{(.*?)\n\}\n", js, re.S)
        self.assertIsNotNone(rerender)
        self.assertIn("state.catalogue", rerender.group(1))

    def test_api_sends_lang_header(self):
        js = (STATIC / "app.js").read_text(encoding="utf-8")
        match = re.search(r"async function api\(path, opts\)\s*\{(.*?)\n\}\n", js, re.S)
        self.assertIsNotNone(match)
        self.assertIn('"X-Kit-Lang": LANG', match.group(1))

    def test_catalogue_rendering_reads_en_and_ko_fields_through_one_helper(self):
        js = (STATIC / "app.js").read_text(encoding="utf-8")
        self.assertIn("function hookField(hook, field)", js)
        self.assertIn('field + "_en"', js)
        self.assertIn('field + "_ko"', js)
        self.assertIn("hookField(hook,", js)

    def test_wiki_language_radio_initialised_from_page_language(self):
        js = (STATIC / "app.js").read_text(encoding="utf-8")
        self.assertIn('wikiLanguage: LANG', js)
        self.assertIn('r.checked = r.value === LANG;', js)


class TestTask617LanguageSwitchKeepsState(unittest.TestCase):
    """Task 6.17 step 1: switching language must not recompute the hook
    selection, and every dynamic piece of UI text (rail labels, gh-whoami,
    finish-summary, wiki-result, visible inline errors) must be re-rendered
    in the new language instead of staying stale."""

    @classmethod
    def setUpClass(cls):
        cls.js = (STATIC / "app.js").read_text(encoding="utf-8")

    def _fn_body(self, name):
        match = re.search(rf"function {name}\(\)\s*\{{(.*?)\n\}}\n", self.js, re.S)
        self.assertIsNotNone(match, f"{name}() not found")
        return match.group(1)

    def test_render_hook_cards_still_recomputes_selection(self):
        # Unchanged contract (Task 5.8): the normal load/install/uninstall
        # path still recomputes state.selectedHooks from the catalogue.
        body = self._fn_body("renderHookCards")
        self.assertIn("state.selectedHooks =", body)

    def test_relabel_hook_cards_does_not_recompute_selection(self):
        body = self._fn_body("relabelHookCards")
        self.assertNotIn("state.selectedHooks =", body)
        self.assertIn("buildHookCards()", body)

    def test_rerender_for_language_calls_relabel_not_render(self):
        body = self._fn_body("rerenderForLanguage")
        self.assertIn("relabelHookCards()", body)
        self.assertNotIn("renderHookCards()", body)

    def test_rail_labels_are_relabelled_on_language_switch(self):
        rerender = self._fn_body("rerenderForLanguage")
        self.assertIn("relabelRail()", rerender)
        relabel_rail = self._fn_body("relabelRail")
        self.assertIn("rail-label", relabel_rail)
        self.assertIn('t("events."', relabel_rail)

    def test_gh_whoami_finish_summary_and_wiki_result_are_rerendered(self):
        rerender = self._fn_body("rerenderForLanguage")
        self.assertIn("renderGhWhoami()", rerender)
        self.assertIn("state.finishResult", rerender)
        self.assertIn("renderFinishSummary(state.finishResult)", rerender)
        self.assertIn("state.wikiResultItems", rerender)
        self.assertIn('renderList("wiki-result", state.wikiResultItems)', rerender)

    def test_gh_whoami_state_is_tracked_and_rendered_from_state(self):
        self.assertIn("state.ghWhoami", self.js)
        render_gh_whoami = self._fn_body("renderGhWhoami")
        self.assertIn("state.ghWhoami", render_gh_whoami)

    def test_visible_inline_errors_are_rerendered_via_i18n_key(self):
        rerender = self._fn_body("rerenderForLanguage")
        self.assertIn("relabelErrors()", rerender)
        relabel_errors = self._fn_body("relabelErrors")
        self.assertIn("state.errors", relabel_errors)
        # showError must accept and record an i18n key so a later language
        # switch can re-derive the message instead of leaving stale text.
        show_error_sig = re.search(r"function showError\(([^)]*)\)", self.js)
        self.assertIsNotNone(show_error_sig)
        self.assertIn("i18nKey", show_error_sig.group(1))
        match = re.search(r"async function withBusy\([^)]*\)\s*\{(.*?)\n\}\n", self.js, re.S)
        self.assertIsNotNone(match, "withBusy not found")
        self.assertIn("e.i18nKey", match.group(1))


class TestTask617DueDisplayNeverShowsPlaceholder(unittest.TestCase):
    """Task 6.17 step 1: #due-display must never render a literal `{n}` —
    at load and after a language switch it must show the actual current
    slider value, driven by syncDue(), not a static data-i18n-args
    template that nothing reads."""

    def test_data_i18n_args_is_removed_from_due_display(self):
        html = (STATIC / "index.html").read_text(encoding="utf-8")
        self.assertNotIn("data-i18n-args", html)
        match = re.search(r'<div class="due-display" id="due-display"[^>]*>', html)
        self.assertIsNotNone(match)
        self.assertNotIn("data-i18n", match.group(0))

    def test_sync_due_is_called_at_boot(self):
        js = (STATIC / "app.js").read_text(encoding="utf-8")
        boot = js[js.index("/* ---------------- boot ---------------- */"):]
        before_init = boot[: boot.index("(async function init()")]
        self.assertIn("syncDue(dueRange.value)", before_init)

    def test_sync_due_is_called_on_language_switch(self):
        js = (STATIC / "app.js").read_text(encoding="utf-8")
        match = re.search(r"function rerenderForLanguage\(\)\s*\{(.*?)\n\}\n", js, re.S)
        self.assertIsNotNone(match)
        self.assertIn("syncDue(dueRange.value)", match.group(1))


class TestTask617TitleAndLangAttrsFollowLanguage(unittest.TestCase):
    """Task 6.17 step 1: <title> follows the language, the 한국어 button
    carries lang="ko", and the language toggle group's aria-label follows
    the language (generic data-i18n / data-i18n-attr, both already driven
    by applyI18n())."""

    @classmethod
    def setUpClass(cls):
        cls.html = (STATIC / "index.html").read_text(encoding="utf-8")

    def test_title_tag_has_data_i18n(self):
        match = re.search(r"<title[^>]*>", self.html)
        self.assertIsNotNone(match)
        self.assertIn('data-i18n="app.title"', match.group(0))

    def test_korean_button_has_lang_ko_attribute(self):
        match = re.search(r'<button[^>]*id="lang-btn-ko"[^>]*>', self.html)
        self.assertIsNotNone(match)
        self.assertIn('lang="ko"', match.group(0))

    def test_lang_toggle_group_aria_label_follows_language(self):
        match = re.search(r'<div class="lang-toggle" id="lang-toggle"[^>]*>', self.html)
        self.assertIsNotNone(match)
        self.assertIn("data-i18n-attr", match.group(0))
        self.assertIn("aria-label", match.group(0))


if __name__ == "__main__":
    unittest.main()
