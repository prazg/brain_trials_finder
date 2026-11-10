# Desktop GUI for Brain Trials Finder (Tkinter, no Streamlit)
# Run with: python desktop_app.py
# Needs: pip install -r requirements-desktop.txt
import csv
import queue
import threading
import tkinter as tk
import urllib.parse
import webbrowser
from tkinter import filedialog, messagebox, ttk
from typing import Any, Dict, List
from xml.sax.saxutils import escape

from ctgov_client import (
    DIAGNOSES,
    CTGovError,
    ensure_list,
    format_site,
    rank,
    search,
    study_locations,
)
from uk_sources import fetch_uk_trials

STATUSES = ["RECRUITING", "NOT_YET_RECRUITING"]
WEB_PAGE = "https://prazg.github.io/brain_trials_finder/"
MAX_ROWS = 500

COUNTRIES = ["", "United Kingdom", "United States", "Australia", "Canada", "China", "France",
             "Germany", "India", "Ireland", "Italy", "Japan", "Netherlands", "Spain"]

# Predefined NIHR UK location options for portal queries
UK_NIHR_LOCATIONS = [
    "", "Birmingham", "Brighton", "Bristol", "Cambridge", "Cardiff", "Coventry", "Dundee",
    "Edinburgh", "Hull", "Leeds", "Liverpool", "London", "Middlesbrough", "Newcastle upon Tyne",
    "Nottingham", "Oxford", "Plymouth", "Preston", "Romford", "Sheffield", "Southampton",
    "Stoke-on-Trent",
]

COLUMNS = [
    ("score", "Score", 55, "center"),
    ("title", "Title", 340, "w"),
    ("status", "Status", 120, "w"),
    ("phases", "Phase", 80, "w"),
    ("site", "Site", 230, "w"),
    ("sponsor", "Sponsor", 190, "w"),
    ("nct", "NCT ID", 105, "w"),
]


# --------------------------------------------------------------------------
# Helpers with no GUI in them, so they can be tested without a display
# --------------------------------------------------------------------------
def contacts_text(study: Dict[str, Any], country: str = "", reasons: str = "") -> str:
    """Plain-text block for the details panel: score reasons, contacts, sites."""
    clm = (study.get("protocolSection") or {}).get("contactsLocationsModule") or {}
    lines: List[str] = []

    if reasons:
        lines.append("Why this score:")
        lines += ["  " + r for r in reasons.split("; ") if r]
        lines.append("")

    centrals = [c for c in ensure_list(clm.get("centralContacts")) if isinstance(c, dict)]
    if centrals:
        lines.append("Central contacts:")
        for c in centrals:
            parts = [str(c.get(k) or "").strip() for k in ("name", "role", "phone", "email")]
            parts = [p for p in parts if p]
            if parts:
                lines.append("  - " + " | ".join(parts))

    officials = [o for o in ensure_list(clm.get("overallOfficials")) if isinstance(o, dict)]
    if officials:
        lines.append("Overall officials:")
        for o in officials:
            parts = [str(o.get(k) or "").strip() for k in ("name", "role", "affiliation")]
            parts = [p for p in parts if p]
            if parts:
                lines.append("  - " + " | ".join(parts))

    preferred = study_locations(study, country) if country else []
    locations = preferred or study_locations(study)
    if locations:
        lines.append(f"Sites in {country} ({len(locations)}):" if preferred else f"Sites ({len(locations)}):")
        for loc in locations[:200]:
            line = format_site(loc) or "Location not published"
            if loc["status"] and loc["status"] != "RECRUITING":
                line += f" ({loc['status'].replace('_', ' ').lower()})"
            lines.append("  - " + line)
            for lc in loc["contacts"]:
                parts = [str(lc.get(k) or "").strip() for k in ("name", "role", "phone", "email")]
                parts = [p for p in parts if p]
                if parts:
                    lines.append("      • " + " | ".join(parts))
        if len(locations) > 200:
            lines.append(f"  …and {len(locations) - 200} more on ClinicalTrials.gov")

    if not centrals and not officials and not locations:
        lines.append("No contacts or sites published for this trial yet.")
    return "\n".join(lines)


def export_pdf(rows: List[Dict[str, Any]], path: str, heading: str = "") -> None:
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

    doc = SimpleDocTemplate(path, pagesize=A4, leftMargin=15 * mm, rightMargin=15 * mm,
                            topMargin=15 * mm, bottomMargin=15 * mm)
    styles = getSampleStyleSheet()
    story = [
        Paragraph("Brain Cancer Trials – Results", styles["Title"]),
        Spacer(1, 6),
        Paragraph(escape(heading or f"Total shown: {len(rows)}"), styles["Normal"]),
        Paragraph("A search aid, not medical advice. Eligibility is decided by the trial team.",
                  styles["Italic"]),
        Spacer(1, 12),
    ]
    # Register text is escaped: Paragraph parses markup, and a title containing
    # "<" or "&" would otherwise break the export.
    for r in rows:
        story.append(Paragraph(f"<b>{escape(str(r.get('title', '')))}</b>", styles["Heading4"]))
        meta = (
            f"NCT: {r.get('nct') or '—'} | Sponsor: {r.get('sponsor') or '—'} | "
            f"Site: {r.get('site') or r.get('city_country') or '—'} | Status: {r.get('status') or '—'} | "
            f"Phase: {r.get('phases') or '—'} | Score: {r.get('score', 0)}"
        )
        story.append(Paragraph(escape(meta), styles["Normal"]))
        if r.get("reasons"):
            story.append(Paragraph(escape("Score: " + r["reasons"]), styles["Normal"]))
        url = r.get("url") or ""
        if url:
            story.append(Paragraph(f"<a href='{escape(url)}' color='blue'>{escape(url)}</a>", styles["Normal"]))
        story.append(Spacer(1, 8))
    doc.build(story)


def export_csv(rows: List[Dict[str, Any]], path: str) -> None:
    cols = ["score", "title", "nct", "url", "status", "phases", "conditions", "sponsor",
            "site", "countries", "reasons"]

    def safe(v: Any) -> Any:
        return "'" + v if isinstance(v, str) and v[:1] in ("=", "+", "-", "@", "\t", "\r") else v

    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=cols)
        writer.writeheader()
        for r in rows:
            writer.writerow({c: safe(r.get(c, "")) for c in cols})


# --------------------------------------------------------------------------
# GUI
# --------------------------------------------------------------------------
class BrainTrialsApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Brain Cancer Trials Finder (Desktop)")
        self.geometry("1240x780")
        self.minsize(900, 560)
        self._search_id = 0
        # Worker threads never touch Tk: they post results here and the main
        # thread collects them. Calling widget methods from a thread is not safe.
        self._inbox: "queue.Queue" = queue.Queue()

        frm = ttk.Frame(self, padding=10)
        frm.pack(fill="x")

        ttk.Label(frm, text="Diagnosis:").grid(row=0, column=0, sticky=tk.W, padx=(0, 6))
        self.diagnosis = tk.StringVar(value="Glioblastoma")
        ttk.Combobox(frm, textvariable=self.diagnosis, values=list(DIAGNOSES.keys()),
                     state="readonly", width=24).grid(row=0, column=1, sticky=tk.W)

        ttk.Label(frm, text="Setting:").grid(row=0, column=2, sticky=tk.W, padx=(16, 6))
        self.setting = tk.StringVar(value="Recurrent")
        ttk.Combobox(frm, textvariable=self.setting, values=["Newly diagnosed", "Recurrent"],
                     state="readonly", width=18).grid(row=0, column=3, sticky=tk.W)

        ttk.Label(frm, text="Age:").grid(row=0, column=4, sticky=tk.W, padx=(16, 6))
        self.age = tk.StringVar(value="55")
        tk.Spinbox(frm, from_=0, to=120, textvariable=self.age, width=6).grid(row=0, column=5, sticky=tk.W)

        ttk.Label(frm, text="KPS:").grid(row=0, column=6, sticky=tk.W, padx=(16, 6))
        self.kps = tk.StringVar(value="80")
        tk.Spinbox(frm, from_=0, to=100, increment=10, textvariable=self.kps, width=6).grid(row=0, column=7, sticky=tk.W)

        self.prior_bev = tk.BooleanVar(value=False)
        ttk.Checkbutton(frm, text="Prior bevacizumab", variable=self.prior_bev).grid(
            row=1, column=1, sticky=tk.W, pady=(6, 0))

        ttk.Label(frm, text="Must mention:").grid(row=1, column=2, sticky=tk.W, padx=(16, 6), pady=(6, 0))
        self.keywords = tk.StringVar(value="")
        kw = ttk.Entry(frm, textvariable=self.keywords, width=30)
        kw.grid(row=1, column=3, sticky=tk.W, pady=(6, 0))

        ttk.Label(frm, text="Site country:").grid(row=1, column=4, sticky=tk.W, padx=(16, 6), pady=(6, 0))
        self.country = tk.StringVar(value="")
        country = ttk.Combobox(frm, textvariable=self.country, values=COUNTRIES, width=18)
        country.grid(row=1, column=5, columnspan=3, sticky=tk.W, pady=(6, 0))

        self.btn_search = ttk.Button(frm, text="Search", command=self.on_search)
        self.btn_search.grid(row=0, column=8, sticky=tk.W, padx=(16, 0))
        ttk.Label(frm, text="Keywords narrow the search (comma separated). Leave country blank for anywhere.",
                  foreground="#555").grid(row=2, column=1, columnspan=7, sticky=tk.W, pady=(6, 0))
        for widget in (kw, country):
            widget.bind("<Return>", lambda _e: self.on_search())

        ukfrm = ttk.Labelframe(self, text="UK shortcuts and export", padding=10)
        ukfrm.pack(fill="x", padx=10)
        self.btn_search_uk = ttk.Button(ukfrm, text="Search UK sites", command=self.on_search_uk)
        self.btn_search_uk.grid(row=0, column=0, sticky=tk.W)
        ttk.Button(ukfrm, text="Open NIHR", command=self.on_open_nihr).grid(row=0, column=1, padx=(12, 0))
        ttk.Label(ukfrm, text="near:").grid(row=0, column=2, padx=(6, 4))
        self.uk_location = tk.StringVar(value="")
        ttk.Combobox(ukfrm, textvariable=self.uk_location, values=UK_NIHR_LOCATIONS, width=20,
                     state="readonly").grid(row=0, column=3)
        ttk.Button(ukfrm, text="Open ISRCTN (UK)", command=self.on_open_isrctn).grid(row=0, column=4, padx=(12, 0))
        ttk.Button(ukfrm, text="Open CRUK", command=self.on_open_cruk).grid(row=0, column=5, padx=(12, 0))
        ttk.Button(ukfrm, text="All registers (web page)",
                   command=lambda: webbrowser.open_new_tab(WEB_PAGE)).grid(row=0, column=6, padx=(12, 0))
        ttk.Button(ukfrm, text="Save PDF", command=self.on_save_pdf).grid(row=0, column=7, padx=(24, 0))
        ttk.Button(ukfrm, text="Save CSV", command=self.on_save_csv).grid(row=0, column=8, padx=(8, 0))

        self.status_lbl = ttk.Label(self, text="Ready", padding=(12, 6, 12, 0))
        self.status_lbl.pack(fill="x")

        treefrm = ttk.Frame(self)
        treefrm.pack(fill="both", expand=True, padx=10, pady=(6, 8))
        self.tree = ttk.Treeview(treefrm, columns=[c[0] for c in COLUMNS], show="headings", height=16)
        for key, text, width, anchor in COLUMNS:
            self.tree.heading(key, text=text, command=lambda k=key: self._sort_by(k))
            self.tree.column(key, width=width, anchor=anchor)
        yscroll = ttk.Scrollbar(treefrm, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=yscroll.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        yscroll.grid(row=0, column=1, sticky="ns")
        treefrm.columnconfigure(0, weight=1)
        treefrm.rowconfigure(0, weight=1)
        self.tree.bind("<Double-1>", self.on_open)
        self.tree.bind("<Return>", self.on_open)
        self.tree.bind("<<TreeviewSelect>>", self.on_select)

        self._row_by_item: Dict[str, Dict[str, Any]] = {}
        self._current_rows: List[Dict[str, Any]] = []
        self._current_country = ""
        self._summary = ""
        self._sort_state: Dict[str, bool] = {}

        infofrm = ttk.Labelframe(
            self, text="Score, contacts and sites (select a row; double-click opens it on ClinicalTrials.gov)",
            padding=10)
        infofrm.pack(fill="both", expand=True, padx=10, pady=(0, 6))
        self.contacts_text = tk.Text(infofrm, height=11, wrap="word")
        self.contacts_text.config(state="disabled")
        scroll = ttk.Scrollbar(infofrm, orient="vertical", command=self.contacts_text.yview)
        self.contacts_text.configure(yscrollcommand=scroll.set)
        self.contacts_text.grid(row=0, column=0, sticky="nsew")
        scroll.grid(row=0, column=1, sticky="ns")
        infofrm.columnconfigure(0, weight=1)
        infofrm.rowconfigure(0, weight=1)

        ttk.Label(self, padding=(12, 0, 12, 8), foreground="#555",
                  text="A search aid, not medical advice. Eligibility is decided by the trial team — "
                       "confirm with the treating clinician and the trial contact.").pack(fill="x")

        self.after(80, self._poll_inbox)
        self.after(100, self.on_search)

    def _poll_inbox(self):
        try:
            while True:
                handler, args = self._inbox.get_nowait()
                handler(*args)
        except queue.Empty:
            pass
        self.after(80, self._poll_inbox)

    # ----- Portal helpers -----
    def _build_portal_query(self) -> str:
        diag = (self.diagnosis.get() or "").strip()
        q = "brain tumour" if not diag or diag.startswith("Any CNS") else diag
        return urllib.parse.quote_plus(q)

    def on_open_nihr(self):
        url = "https://www.bepartofresearch.nihr.ac.uk/results/search-results?query=" + self._build_portal_query()
        loc_txt = (self.uk_location.get() or "").strip()
        if loc_txt:
            url += "&location=" + urllib.parse.quote_plus(loc_txt)
        webbrowser.open_new_tab(url)

    def on_open_isrctn(self):
        webbrowser.open_new_tab(
            f"https://www.isrctn.com/search?q={self._build_portal_query()}&countries=United%20Kingdom")

    def on_open_cruk(self):
        webbrowser.open_new_tab(f"https://find.cancerresearchuk.org/clinical-trials?q={self._build_portal_query()}")

    # ----- Reading the form -----
    def _read_number(self, var: tk.StringVar, name: str, low: int, high: int) -> int:
        raw = (var.get() or "").strip()
        try:
            value = int(float(raw))
        except ValueError:
            raise ValueError(f"{name} must be a number between {low} and {high}.") from None
        if not low <= value <= high:
            raise ValueError(f"{name} must be between {low} and {high}.")
        return value

    def _read_intake(self) -> Dict[str, Any]:
        return {
            "diagnosis": self.diagnosis.get(),
            "setting": self.setting.get(),
            "age": self._read_number(self.age, "Age", 0, 120),
            "kps": self._read_number(self.kps, "KPS", 0, 100),
            "prior_bev": bool(self.prior_bev.get()),
            "keywords": self.keywords.get().strip(),
            "country": self.country.get().strip(),
        }

    # ----- Actions -----
    def on_open(self, event=None):
        for iid in self.tree.selection():
            url = (self._row_by_item.get(iid) or {}).get("url")
            if url:
                webbrowser.open_new_tab(url)
                break

    def on_select(self, event=None):
        sel = self.tree.selection()
        if not sel:
            return
        row = self._row_by_item.get(sel[0])
        if row and row.get("study"):
            self._set_details(contacts_text(row["study"], self._current_country, row.get("reasons", "")))

    def _start(self, message: str) -> int:
        self._search_id += 1
        self.btn_search.configure(state=tk.DISABLED)
        self.btn_search_uk.configure(state=tk.DISABLED)
        self.status_lbl.configure(text=message)
        return self._search_id

    def on_search(self):
        try:
            intake = self._read_intake()
        except ValueError as exc:
            messagebox.showwarning("Check the form", str(exc))
            return
        search_id = self._start("Searching ClinicalTrials.gov…")

        def worker():
            try:
                studies, total = search(intake["diagnosis"], intake["keywords"], country=intake["country"],
                                        statuses=STATUSES, page_size=100, max_pages=5)
                rows, hidden = rank(studies, intake)
                self._inbox.put((self._render_rows, (search_id, rows, hidden, total, len(studies), intake["country"])))
            except CTGovError as exc:
                self._inbox.put((self._show_error, (search_id, str(exc))))
            except Exception as exc:  # a bug should be reported, not freeze the buttons
                self._inbox.put((self._show_error, (search_id, f"Unexpected error: {exc}")))

        threading.Thread(target=worker, daemon=True).start()

    def on_search_uk(self):
        try:
            intake = self._read_intake()
        except ValueError as exc:
            messagebox.showwarning("Check the form", str(exc))
            return
        search_id = self._start("Searching ClinicalTrials.gov for UK sites…")

        def worker():
            try:
                rows, total, hidden = fetch_uk_trials(intake["diagnosis"], intake["keywords"], intake)
                self._inbox.put((self._render_rows, (search_id, rows, hidden, total, len(rows) + hidden, "United Kingdom")))
            except CTGovError as exc:
                self._inbox.put((self._show_error, (search_id, str(exc))))
            except Exception as exc:
                self._inbox.put((self._show_error, (search_id, f"Unexpected error: {exc}")))

        threading.Thread(target=worker, daemon=True).start()

    # ----- Rendering & details -----
    def _finish(self):
        self.btn_search.configure(state=tk.NORMAL)
        self.btn_search_uk.configure(state=tk.NORMAL)

    def _show_error(self, search_id: int, message: str):
        if search_id != self._search_id:
            return
        self._finish()
        self.status_lbl.configure(text="Search failed. The previous results are still shown.")
        messagebox.showerror("Search failed", message)

    def _set_details(self, text: str):
        self.contacts_text.config(state="normal")
        self.contacts_text.delete("1.0", tk.END)
        self.contacts_text.insert(tk.END, text)
        self.contacts_text.config(state="disabled")

    def _fill_tree(self, rows: List[Dict[str, Any]]):
        for iid in self.tree.get_children():
            self.tree.delete(iid)
        self._row_by_item.clear()
        for r in rows[:MAX_ROWS]:
            iid = self.tree.insert("", "end", values=[r.get(c[0], "") for c in COLUMNS])
            self._row_by_item[iid] = r

    def _render_rows(self, search_id: int, rows: List[Dict[str, Any]], hidden: int, total: int,
                     fetched: int, country: str):
        if search_id != self._search_id:   # an older search finishing late
            return
        self._current_rows = rows[:]
        self._current_country = country
        self._sort_state.clear()
        self._fill_tree(rows)

        where = f" with a site in {country}" if country else ""
        txt = f"{len(rows)} recruiting trial{'' if len(rows) == 1 else 's'}{where}."
        if total > fetched:
            txt += f" The register holds {total}; the first {fetched} were fetched."
        if hidden:
            txt += f" {hidden} hidden as unrelated to brain or spine tumours."
        if not rows:
            txt += " Try removing the keywords or the country."
        self._summary = txt
        self.status_lbl.configure(text=txt)
        self._set_details("Select a trial to see why it scored as it did, its contacts and its sites."
                          if rows else "")
        self._finish()

    def _sort_by(self, key: str):
        descending = not self._sort_state.get(key, key != "score")
        self._sort_state = {key: descending}
        self._current_rows.sort(
            key=lambda r: (r.get(key) if key == "score" else str(r.get(key, "")).lower()),
            reverse=descending)
        self._fill_tree(self._current_rows)

    # ----- Export -----
    def _ask_path(self, title: str, ext: str, kind: str) -> str:
        if not self._current_rows:
            messagebox.showinfo(title, "No results to export. Run a search first.")
            return ""
        return filedialog.asksaveasfilename(
            title=title, defaultextension=ext, filetypes=[(kind, "*" + ext)],
            initialfile="brain_trials_results" + ext) or ""

    def on_save_pdf(self):
        path = self._ask_path("Save PDF", ".pdf", "PDF files")
        if not path:
            return
        try:
            export_pdf(self._current_rows, path, self._summary)
            messagebox.showinfo("Save PDF", f"Saved: {path}")
        except ImportError:
            messagebox.showerror("Save PDF", "PDF export needs the reportlab package:\n\npip install reportlab")
        except Exception as e:
            messagebox.showerror("Save PDF", f"Failed to create PDF.\n{e}")

    def on_save_csv(self):
        path = self._ask_path("Save CSV", ".csv", "CSV files")
        if not path:
            return
        try:
            export_csv(self._current_rows, path)
            messagebox.showinfo("Save CSV", f"Saved: {path}")
        except OSError as e:
            messagebox.showerror("Save CSV", f"Could not write the file.\n{e}")


if __name__ == "__main__":
    app = BrainTrialsApp()
    app.mainloop()
