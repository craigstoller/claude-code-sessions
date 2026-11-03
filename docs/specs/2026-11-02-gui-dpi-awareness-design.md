# GUI DPI awareness - the window renders crisp at the display's scale (design)

**Status: draft, 2026-11-02. A three-round panel review has been applied; see "Review" near
the end. Decisions D1-D6 were ruled by the maintainer on 2026-11-02, each as recommended.
Implemented 2026-11-02 on the maintainer's go-ahead. The release (0.16.6) waits on A5, the
maintainer's real 150 % session.**
This is finding F8 of the 2026-09-01 UX/UI audit of the "Claude sessions" window
(`ccs-gui`), the one item the GUI polish release (0.16.0) deliberately left for its own
spec. Every other audit finding shipped in 0.16.0.

Names below are the fake cast (`alice@example.com`, `bob@example.com`, `ACME-REVIEW
session`, `Northwind`). Line numbers are at `main` `47cec4b` (0.16.5 plus README commits).

**Basis marks.** Every factual claim carries one:

- **[meas]** measured on the operator's Windows machine on 2026-11-02 by a probe described
  in the appendix. The probes were throwaway scratch scripts: none is committed, none
  touched a session store, and the only real window they opened was opened read-only and
  closed unpressed.
- **[doc]** read from a document or from source, which the claim names: this repo, CPython's
  `idlelib` and `ctypes` documentation, Tk 9.0.4's theme scripts, or Microsoft's
  documentation.
- **[infer]** reasoned from the above, not measured.
- **[open]** not settled. Each [open] fed a decision at the end, and the maintainer has
  since ruled on all of them; the facts themselves stay unmeasured.

## Why, and why now

The audit measured, on 2026-09-01, a primary display of 3840x2400 at 250 %, and Tk
reporting 96 px/in and a 1536x960 screen under the installed launcher. The window process
was not DPI-aware, so Windows painted it at 96 dpi and stretched the bitmap 2.5x. Text is
soft, and the ttk widgets are blurred. The audit proposed a narrow change: declare *system*
DPI awareness through ctypes, guarded, and apply one factor to the pixel constants that
remain. It set three preconditions and criteria: F2's wrap-to-width helper first, then the
layout harness passing at 100 %, 150 % and 250 %, the same on-screen size at 250 %, and
crisp text.

Re-measured today, the defect is unchanged, and the precondition holds:

- **Still not DPI-aware [meas].** The installed 0.16.5 chain is `ccs-gui.exe` (the venv's
  GUI launcher) -> the venv's `pythonw.exe` redirector -> the base interpreter's
  `pythonw.exe` (Python 3.14.10, Tk 9.0.4), which owns the window. Read from outside by a
  per-monitor-aware observer while the real window was open:
  - `GetProcessDpiAwareness` = 0 (unaware);
  - `GetDpiForWindow` = 96;
  - client area 2350x1600 physical pixels, which is 940x640 x 2.5.

  The window opened, planned (reads only) and was closed with `WM_CLOSE` on the idle path;
  nothing was pressed.
- **The display is as the audit found it [meas].** One monitor was connected, 3840x2400,
  Windows' applied DPI 240 (250 %). Inside the unaware process Tk reports 96.09 px/in, a
  1536x960 screen and a 1536x912 work area.
- **F2's helper shipped in 0.16.0 [doc].** `_wrap_to` (L2309) arrived in `7add94d`, which
  is first contained in `v0.16.0`, together with:
  - the work-area clamp (`_work_area`, `_fit`, L88-126);
  - the bottom-pinned bars;
  - `tools/check_gui_layout.py`, whose 74 checks pass unchanged today [meas].

  Labels no longer carry `wraplength=880`. The pixel constants that remain are the ones
  listed in Change 3.

The README already promises this item: "DPI awareness, the audit's last item, is its own
later release; the harness it needed now exists" [doc, README L959-960, in the 0.16.0
entry of its release story].

## What the design rests on - measured 2026-11-02

Each probe was run in a fresh process under the installed interpreter, once unaware (as
shipped) and once after `SetProcessDpiAwareness(1)`.

| Quantity | Unaware (as shipped) | System-aware | Basis |
|---|---|---|---|
| `SetProcessDpiAwareness(1)` through `ctypes.OleDLL` | not called | returns; `GetProcessDpiAwareness` = 1; a second call raises `OSError` 0x80070005 (`E_ACCESSDENIED`) | [meas] |
| `GetDpiForSystem()`, `GetDeviceCaps(LOGPIXELSX)`, `winfo_pixels("1i")` | 96, 96, 96 | 240, 240, 240 | [meas] |
| `winfo_fpixels("1i")` | 96.0946 | 240.2365 | [meas] |
| Tk `tk scaling` | 1.3346 | 3.3366 | [meas] |
| screen / work area seen by the process | 1536x960 / 1536x912 | 3840x2400 / 3840x2280 | [meas] |
| `TkDefaultFont` (and the other named fonts) | Segoe UI, size **+9** (points) | the same, size +9 | [meas] |
| line height, Segoe UI 9 / 11 bold / Consolas 9 | 15 / 20 / 14 px | 41 / 50 / 36 px (x2.73 / x2.5 / x2.57) | [meas] |
| width of "Only sessions whose title contains:" (default font) | 182 px | 458 px (x2.52) | [meas] |
| ttk (vista) parts: scrollbar width, checkbutton height, button height, entry height | 17, 21, 25, 21 | 43, 53, 55, 47 | [meas] |
| ttk Notebook side border | 2 px each side | 2 px each side (not scaled) | [meas] |
| vista tab label inset, horizontal and vertical | 4 px, 4 px | 4 px, 4 px (not scaled) | [meas] |
| window chrome, from `GetSystemMetrics` (width x height) | about 16x39 | about 32x88 | [meas] |

What follows from the table:

1. **The awareness call works in the installed interpreter**, and nothing else in the
   chain declares awareness: the probe found 0 in the unaware process, and the base
   interpreter's `pythonw.exe` is the window's process [meas]. The package does not own
   that executable or its manifest, so the API call is the route the package controls
   [infer].
2. **Fonts and theme parts scale by themselves on Tk 9.0.4.** Every font the window uses is
   point-sized: the explicit ones ("Segoe UI" 9/11, Consolas 9) and Tk 9.0.4's named
   fonts, which ttk labels and buttons inherit [meas]. Tk 9's vista theme sizes most of
   its paddings in points (`TButton -padding 0.75p`, `TCheckbutton -padding 1.5p`) [doc,
   `vistaTheme.tcl` in Tk 9.0.4], and the native parts come back at the process's DPI
   [meas]. So CPython's own HiDPI font fix for IDLE, `idlelib.util.fix_scaling`, which
   converts *negative* (pixel) named-font sizes to points [doc], has nothing to do on Tk
   9.0.4. None of this is measured on Tk 8.6 (decision D3).
3. **Scaling is not linear.** At 250 % the line height grows 2.57-2.73x, buttons and
   entries 2.2x, and text width about 2.5x [meas]. A layout that fits at 100 % is
   therefore not *guaranteed* to fit at 250 % by multiplying its constants: it has to be
   measured at the real scale (Change 6).
4. **The factor must not come from `winfo_fpixels`.** Tk derives its DPI from the
   screen's width in millimetres, which Windows rounds to a whole millimetre. 3840 px over
   406 mm gives 240.24 px/in, and 1536 px over 406 mm gives 96.09 [meas, and the arithmetic
   reproduces both values]. The audit's `winfo_fpixels("1i") / 96` would give factors of
   1.00098 and 2.5025: 940 would become 941 at 100 %, moving every existing pin, and 2352
   at 250 %. The OS's integer DPI, and Tk's rounded `winfo_pixels("1i")`, give exactly 1.0
   and 2.5 (Change 2).
5. **Two theme values do not scale and one of them shows.** The vista tab label inset
   stays 4 px at both DPIs [meas]. At 250 % the tab labels touch their tab edges, a
   defect the before/after screenshots show and the geometry harness cannot see (Change 4).
   `TNotebook -tabmargins {2 2 2 0}` and `TEntry -padding {1 1 1 1}` are pixel values in
   the theme scripts [doc]. They render as hairline margins that the screenshots show as
   acceptable [meas], and they are left alone.

## The prototype that de-risks it

A scratch copy of the window, never committed, applied the factor at every site Change 3
lists. It made 86 helper calls: 85 rewritten literals and `PAD` uses, plus the one
`px(wrap)` where `_Dialog` uses its logical default. A scratch copy of the layout harness
expressed its pins in logical pixels times the factor, by the rule in Change 6. Results
[meas]:

| Run | Result |
|---|---|
| Today's harness, today's window, unaware | 74/74 |
| Today's harness, today's window, system-aware, **no factor** | 14 checks pass, then controls lie outside their tabs (a 940x640-physical window holding 250 % text), and the run crashes on an unfitted label's empty `wraplength` |
| Prototype harness, prototype window, unaware (factor 1.0) | 74/74, identical to today's |
| Prototype harness, prototype window, system-aware (factor 2.5) | 74/74 |
| The same with the Change 4 tab inset added | 74/74 at both 1.0 and 2.5 |
| Opening client size, prototype, aware, at 250 % | 2350x1600 physical, the same as the shipped window's measured size |

Screenshots of the harness's fake-cast fixture, before and after, show soft stretched text
before and crisp text after. With the Change 4 inset, the tab strip matches the 100 % look
[meas, visual]. They are kept hub-side, not committed (see "Calls made by the author").

**What could not be measured on this machine: scales other than 100 % and 250 %.** Three
attempts to simulate 150 % inside one process all failed to be faithful [meas]:

- Tk's named fonts do not follow a later `tk scaling` change until each is reconfigured.
- The theme's native parts always follow the process's real DPI, never `tk scaling`.
- An unaware process cannot open a window larger than its 1536x960 virtual screen.

So an aware base overstates the theme parts and fails at 1.25-2.0, while an unaware base
passes at 1.25 and is capped by the screen above that. Neither brackets a real 150 %
session.

One number from those runs does survive: a font's line height at a given pixel size. In
the probe that set `tk scaling` to 150 %'s value and reconfigured the fonts, Segoe UI 9 pt
realized at 18 px had a line height of 25 px [meas]. That is a property of the font at
that pixel size, not of the layout [infer].

Microsoft documents that a system-aware process gets "the DPI of the primary display at the
time the current user session was started", and its own testing advice is to change the
display, sign out and test after signing back in [doc, Microsoft Learn, "High DPI Desktop
Application Development on Windows"]. A real 150 % check therefore costs a scale change and
a sign-out, twice. That is decision D4.

## Goals, non-goals, anti-goals

**Goals.**

- Crisp text and widgets on the operator's 250 % display.
- The same on-screen size there as today. Today's stretched window measures
  `f x min(940, work/f - 40)` physical pixels across, and the scaled clamp gives
  `min(P(940), work - P(40))`. Those are the same number on any display, up to rounding,
  and likewise for the height [infer, arithmetic]. On the default work area that is client
  2350x1600 [meas].
- At 100 % a layout identical to today's, with every existing harness pin unchanged by
  construction.
- No change to what the window does: which engine calls it makes, in what order, under
  which guards. Only how it is drawn changes.

**Non-goals** (each argued under "Non-goals" at the end):

- per-monitor awareness;
- following Windows' text-size accessibility setting;
- HiDPI on macOS or Linux;
- any font-size or layout redesign.

**Anti-goals.** This fails, even if the text gets crisp, if any of these happens:

- the 100 % layout moves by a pixel;
- the window opens larger than the work area. The only exception is the existing
  `_ABSOLUTE_MIN` floor, which 0.16.0 already lets win on a work area under about 280x290
  logical pixels [doc, `_fit`];
- the factor and Windows' own stretch ever apply together, so the window is scaled twice;
- this change makes the process aware while the factor stays 1.0, which is the
  prototype's measured broken state. A host that makes the process aware on an unsupported
  Tk is the same state as 0.16.5 on that host, and is a residual;
- the awareness call stops the window from opening;
- a Tk the change was not measured on gets tiny or doubled fonts.

## The principles carried over unchanged

From the module docstring and the GUI designs:

- the thin shell over the library;
- nothing written before an action button;
- refusals verbatim;
- friction that is a safeguard stays.

This spec touches none of them. It changes how the window is drawn, not what it does:

- no engine call is added, removed or reordered;
- no string a harness pins changes, apart from the layout harness's own pixel expectations.

The ctypes calls it adds are one process-wide flag set before the window exists, and OS
reads of the DPI and of the awareness state.

## Change 1 - one initialization contract: declare, then read what took effect

Three module functions, beside `_work_area`, all importable without tkinter, and none of
them can raise.

**`_dpi_supported()`** is true only on Windows, with tkinter importable (`tk` is not
`None`), and with `tk.TkVersion` in a set of measured versions, today `{9.0}` (decision
D3). A Tk 9.1 or later is supported only once the probes and both harness runs have been
repeated on it; widening the set is then a one-line change. Every DPI behaviour in this
spec is behind it. When it is false, the window behaves exactly as 0.16.5 does, whatever the host
process has set: no call, factor 1.0, no tab style [infer]. The check reads the module
constant `TkVersion`, which exists before any root. It runs inside the function's own
guard, so a box without tkinter cannot raise there [doc: the module binds `tk = None` when
the import fails].

**`_declare_dpi_awareness()`** is called in `main()` after the missing-tkinter branch and
immediately before `root = tk.Tk()` (L4693), on the window path only. The shortcut flags
and the missing-tkinter path never create a Tk window. It returns nothing.

- **What it calls**, when `_dpi_supported()` holds:
  `ctypes.OleDLL("shcore").SetProcessDpiAwareness(1)`, that is
  `PROCESS_SYSTEM_DPI_AWARE`. It catches `ImportError`, `AttributeError` and `OSError`.
- **Prior art.** This is the stdlib's own pattern: `idlelib.util.fix_win_hidpi()` makes the
  same call with the same guard, before any Tk operation [doc, CPython 3.14
  `Lib/idlelib/util.py`].
- **Why no `restype` setup is needed.** `OleDLL` functions return `HRESULT` by default and
  raise `OSError` on a failing one [doc, ctypes documentation; meas: the default `restype`
  is `ctypes.HRESULT`, and a second call raised `OSError` 0x80070005].
- **"Already set" is absorbed.** The API returns `E_ACCESSDENIED` when awareness "is
  already set, either by calling this API previously or through the application (.exe)
  manifest" [doc, Microsoft Learn, `SetProcessDpiAwareness`].
- **Why not a manifest.** Microsoft recommends a manifest over this call [doc, same page],
  and the package cannot use one: the window's process is the base interpreter's
  `pythonw.exe`, which the package does not build [meas]. The call is made before the
  first window exists, which is the condition that page sets.
- **System, not per-monitor.** System awareness lays the window out once, at the system
  DPI, and Windows stretches it on a monitor of a different DPI [doc, Microsoft Learn].
  Per-monitor awareness would need the window to re-lay itself out on `WM_DPICHANGED`,
  which this spec neither relies on Tk doing nor tests. The audit chose system awareness
  for the same reasons [doc, audit F8]. What the choice costs on a second monitor is the
  subject of decision D1.

**`_dpi_awareness()`** reads the effective process awareness after the root exists:
`GetProcessDpiAwareness(None)`, giving 0, 1 or 2, or `None` if it cannot be read. The
window does not branch on it. The harness asserts it, and it is the honest answer to "what
took effect", since the call can be absorbed:

- **1** is the designed state.
- **2** (per-monitor) can only come from the host, for example a future interpreter's
  manifest.
  - The factor is still the *system* DPI, the primary display's at sign-in, not the DPI of
    the monitor the window lands on [doc, Microsoft Learn, `GetDpiForSystem`].
  - So on a monitor of another DPI the window, and every dialog it opens later, is drawn
    at the system size and is not stretched by Windows: too large or too small there
    [infer].
  - That is a residual, not a supported mode.

## Change 2 - one factor, read from the process's own view of the DPI

`_scale_factor(root)`, stubbable like `_work_area`:

- **Unsupported:** if `_dpi_supported()` is false, 1.0.
- **Otherwise, the first of these that answers above zero, over 96:**
  - `user32.GetDpiForSystem()`. It needs Windows 10 1607 [doc, Microsoft Learn,
    `GetDpiForSystem`], and returns 0 on failure rather than raising [infer, from the API's
    integer return], so zero counts as no answer.
  - `GetDeviceCaps(GetDC(None), LOGPIXELSX)`, with the screen DC released in a `finally`.
    `SetProcessDpiAwareness` needs only Windows 8.1 [doc], so this covers the versions in
    between.
  - Tk's own `root.winfo_pixels("1i")`, which cannot fail. It is Tk's rounded reading of
    the same DPI it realized the fonts at [meas: exactly 96 and 240]. On some panel sizes
    the millimetre rounding could put it one off the OS's DPI [infer], but it is reached
    only when both OS reads have failed.
- **No floor.** The factor is that DPI over 96, whatever it is. A DPI below 96 needs a
  setting outside Windows' scale list [infer]. Even then a factor below 1.0 matches the
  fonts Tk realized, where a floor of 1.0 would recreate the mismatch the anti-goals
  forbid.

`SyncApp.__init__` reads it once into `self.scale`, before `_work_area`. Microsoft's page
advises against caching the system DPI [doc]. It is read once here on purpose: the window
builds its layout once, and a system-aware process's system DPI is the primary display's at
sign-in [doc, Microsoft Learn], so a later read could only disagree with widgets already
built.

The property the anti-goals ask for:

- **Every source is the process's own view, never the display's.** An unaware process is
  told 96 even on a 250 % display [meas; and `GetDpiForSystem` returns 96 to an unaware
  thread by contract - doc].
- So the factor is 1.0 exactly when Windows is stretching the window, and the real DPI
  exactly when it is not. That holds whether this code, the host or a manifest set the
  awareness.
- **Precondition.** The fonts and the factor must read the same DPI. They did in every
  state measured here [meas]. After a scale change without signing out, the identity
  rests on Windows also virtualizing the APIs Tk reads its DPI from (`GetDeviceCaps`, the
  screen size) to the sign-in DPI. Microsoft describes exactly that for system-aware
  processes: Windows "will DPI-scale some API calls into the coordinate space that the
  HWND would be using if it were running at its original DPI scale factor" [doc,
  Microsoft Learn]. That those calls include the ones Tk uses is [infer]. Decision D4's
  session probes it before signing out, at no extra cost.
- Because the last source is Tk's own reading, the factor cannot fall back to 1.0 in a
  process that is aware. That closes the one combination the prototype measured as broken:
  awareness taken, constants unscaled.

Not `winfo_fpixels("1i") / 96` (the audit's proposal), for the millimetre-rounding reason
in the table above (decision D2).

## Change 3 - the factor at every pixel constant

**The rule.** Source constants stay in *logical* pixels, the 96-dpi unit every comment,
spec and harness pin already uses, and are scaled at the point where they enter Tk:
one module helper, `_px(n, scale) = int(round(n * scale))`. `SyncApp.px(n)` calls it with
`self.scale`. `_Dialog` already receives the app as its first argument [doc, L1663], and
reads `app.px`. The module's size functions call `_px` with their own `scale`.

**Units.** Geometry is in the process's own pixels: physical when aware, logical when not.

- `_work_area(root, scale=1.0)` returns the work area in those pixels. On Windows that is
  `SPI_GETWORKAREA`, which already answers in the process's pixels [meas: 1536x912 unaware,
  3840x2280 aware].
- Off Windows, the function subtracts `px(NON_WINDOWS_MARGIN)` from `winfo_screenwidth/height`.
  Its "logical pixels" docstring changes to say so.
- `work` is never scaled again. Scaling it would be the double scaling the anti-goals rule
  out.
- The pure size functions gain a `scale` parameter that scales the size, `WINDOW_CHROME`
  and `_ABSOLUTE_MIN`:
  - `_fit(size, work, scale=1.0)`;
  - `_initial_geometry(work, scale=1.0)`;
  - `_min_size(work, scale=1.0)`.
- The precedence is today's, scaled: `max(px(_ABSOLUTE_MIN), min(px(size), work -
  px(chrome)))`. The floor wins over a work area too small for it, as it does in 0.16.0
  [doc, `_fit`].

At 1.0, `px` is the identity on integers, so the 100 % layout cannot move [infer, and the
prototype's 74/74 at 1.0 measured it]. Rounding is Python's `round` (half to even): at
2.5, 1 -> 2, 3 -> 8 (7.5 rounds to the even 8), 871 -> 2178.

**The inventory**, by an AST scan of the source for numeric literals:

- in pixel-valued keyword arguments (`padx`, `pady`, `ipadx`, `ipady`, `padding`,
  `wraplength`, `yscrollincrement`, `borderwidth`, `highlightthickness`, `margin`);
- in module size constants;
- in geometry arithmetic.

Line numbers at `47cec4b`:

| Kind | Sites | Logical values | Lines |
|---|---|---|---|
| Module size constants | 6 names | `PAD` 10; `DEFAULT_SIZE` 940x640; `FIT_FLOOR` 760x420; `WINDOW_CHROME` 40x48; `NON_WINDOWS_MARGIN` 96; `_ABSOLUTE_MIN` 240 | 67, 81-85 |
| Uses of `PAD` | 23 | 10 | outer frame 1789; the three tabs 1827-1829; window and Health bars 1799, 2160; dialogs 1672, 1674, 2698, 2710, 2711, 3204, 3219, 3239, 4163, 4169 |
| `padx` / `pady` / ttk `padding` literals | 46 | 2, 3, 4, 6, 8, 12 | 1672, 1847, 1851, 1862, 1886, 1919, 1962, 1976, 2004, 2016, 2047, 2053, 2058, 2064, 2068, 2083, 2096, 2149, 2150, 2154, 2164, 2177, 2645, 2651, 2662, 2675, 2698, 2710, 2711, 3135, 3204, 3219, 3239, 3276, 3560, 3858, 3862, 3898, 3902, 3910, 3923, 4163, 4167 |
| Wrap widths | 7 | `_Dialog` default 640 (1663, kept logical in the signature and scaled where it is used, at 1671); 600 (2699); 560 (2712); 620 (3205, 3220); 680 (4164); the `_wrap_to` floor 120 (2329) | |
| Margins added to a measured width | 3 | `+ 8` (2075), `+ 12` (2652), the hold column `+ 6` (3914) | |
| Sash and holds arithmetic | 3 | `default=40` (2369), `+ 8` (2373), `- 8` (2391) | |
| Canvas scroll unit | 1 | `yscrollincrement=24` (1936) | |
| Text-pane borders | 3 | `borderwidth=1` (1902, 2139, 2180) | |

**Reconciling the counts.** That is 86 use sites plus the module constants:

- the 59 non-zero literals in pixel-valued keyword arguments (the 46 paddings, the 7 wrap
  widths, the `+ 8` and `+ 12` margins, the scroll unit and the three borders);
- the 23 `PAD` uses;
- 4 literals in geometry arithmetic (`default=40`, `+ 8`, `- 8`, `+ 6`).

The prototype rewrote 85 of them in place. The 86th is the `_Dialog` default, which stays
logical in the signature and is scaled at its one use. The prototype therefore made 86
helper calls in widget code. The 40 zero literals in the same arguments (`(0, 6)` and the
like) scale to themselves and need nothing.

**Not pixels, left alone** (listed so a reviewer can see they were checked):

- Entry widths 34 (2052) and 40 (3922) are characters.
- Text heights 8 (1900, 2138, 2178) and `min(len, 14)` (4170), and the stage-1 width 100
  (4171), are lines and characters.
- `SCOREBOARD_LINES` 7 is lines.
- Every font size is points.
- The wheel's `delta` 120 (`_on_holds_wheel`) is the OS's notch unit.
- `max(0, ...)` in `busy()` is a counter.
- `highlightthickness=0` is zero.

All of these follow the font, or are not lengths [doc, and the font measurements above].

**Pixels deliberately not scaled, with the reason for each:**

- **Tk's own defaults that the source never sets** - the Text panes' `padx` and `pady` of
  1 [meas]. They are inner hairlines, outside this rule because the source holds no
  constant for them. `_text_extra_px` reads them back, so the sash arithmetic stays
  consistent.
- **`_on_sash_release`'s `> 1` drag tolerance.** It means "the sash moved", which a
  physical pixel answers at any DPI.
- **The one physical constant `VISTA_TAB_INSET` of Change 4.**

The character and line counts listed above are a third class, outside the rule because
they are not pixels. Change 5 names them, so its guard does not mistake them for pixels.

**Derived, and must not be scaled again** (each already reads a scaled or font-driven
value). Wrapping any of them in `px` would scale twice:

- `_wrap_to`'s default margin, read back from the container's `padding`;
- `_line_px`, from font metrics;
- `_text_extra_px`, from the Text's own `borderwidth`, `pady` and `highlightthickness`;
- the measured half of every `winfo_reqwidth() + n` margin (only the `n` is scaled);
- `_Dialog.run`'s centring;
- the canvas window width bound to `event.width`;
- `_page_holds`;
- every `winfo_*` read;
- `work`.

**Two author's calls inside the rule:**

- **Borders scale with everything else.** `borderwidth=1` becomes 2 physical px at 250 %.
  That is closer to today's stretched look than a 1-px hairline, and it keeps the rule
  free of exceptions among the source's own constants, which is what makes Change 5's guard
  simple. `_text_extra_px` reads the value back, so the sash arithmetic follows.
- **`WINDOW_CHROME` stays a scaled margin rather than a measured one.**
  - 40x48 at 1.0 and 100x120 at 2.5 both exceed the measured chrome (about 16x39 and
    32x88) [meas].
  - Scaling it is what makes the goal's size identity exact: today's clamp, stretched,
    equals the scaled clamp.
  - Reading the chrome from `GetSystemMetrics` would change today's clamp at 1.0, which the
    anti-goals forbid.

## Change 4 - the one theme value that shows: the tab label inset

The vista notebook tab's label inset is 4 px on both axes at both DPIs [meas]. At 250 %
"Level", "Health" and "One session" sit flush against their tab edges, while today's
stretched window shows a margin [meas, screenshot].

When `self.scale > 1.0` and the theme in use is `vista`, the only theme measured,
`SyncApp.__init__` configures the tab style once, from a named physical constant:

```python
# px, physical: the vista tab label's own inset on both axes, measured unscaled at
# 96 and 240 dpi on Tk 9.0.4, 2026-11-02 (re-measured by the layout harness, pin (e))
VISTA_TAB_INSET = 4
...
extra = self.px(VISTA_TAB_INSET) - VISTA_TAB_INSET
ttk.Style(root).configure("TNotebook.Tab", padding=(extra, extra))
```

Style padding adds to the element's own inset; it does not replace it [meas]. With (6, 6)
at 2.5, the label's first pixel moved from (4, 4) to (10, 10), which is P(4) on both axes.
The prototype ran exactly this:

- the tab strip matched the 100 % look in the screenshot;
- the harness stayed 74/74 at both factors [meas].

At 1.0, or on any other theme, nothing is configured and the theme's own look is untouched.

The harness opens each window in a fresh Tcl interpreter (a new `tk.Tk()`), so a style set
at one factor never leaks into a window built at another [doc, `open_app`].

`TNotebook -tabmargins {2 2 2 0}` and `TEntry -padding {1 1 1 1}` stay as the theme sets
them [doc]. They are hairline margins that render acceptably at 250 % [meas, screenshot].
If the acceptance screenshot review finds an entry's text inset too tight, the same pattern
applies to `TEntry` (author's call to leave it now).

## Change 5 - guards against new unscaled constants, and against a missing call

A pure test (`tests/test_gui_dpi.py`, importable without tkinter) parses the window source
with `ast`. It fails on any of these:

- **A non-zero numeric literal or bare `PAD` in a pixel argument** - inside a pixel-valued
  keyword argument (the list in Change 3) and not inside a call to the scaling helper.
- **An unscaled literal in the geometry arithmetic** - in `_wrap_to`, `_holds_min_px`,
  `_clamp_sash` and `_render_holds`, the four methods whose arithmetic the inventory
  found, and in the module's size functions `_fit`, `_initial_geometry`, `_min_size` and
  `_work_area`. There, a non-zero numeric literal that is an operand of `+` or `-`, an argument
  of `max` or `min`, or the value of a `default=` keyword argument (as in `max(...,
  default=40)`; a function's own parameter default is not one) fails unless it is inside
  the helper. The helper is a call to `_px` or to `.px`. This covers the sash and holds arithmetic, whose literals sit beside local
  names rather than `winfo_*` calls.
- **A missing or late awareness call** - `main()` must call `_declare_dpi_awareness()`
  before `tk.Tk()`, checked on the AST of `main`'s body. Without this, every other test
  and both harness runs would pass with the shipped launcher still unaware, since the
  harness declares awareness itself (Change 6).

**Its exceptions, each by name:**

- `VISTA_TAB_INSET`, the one physical constant. Its definition carries the measurement's
  date and Tk version in a comment, and the harness re-measures it (Change 6, pin (e)).
- Character and line counts: any literal inside a `width=` or `height=` argument of an
  `Entry` or `Text`, `min(len(mappings), 14)` included. Scaling 14 to 35 would make the
  stage-1 list 35 lines tall at 250 %, each line already grown with its font.
- `_Dialog`'s parameter default `wrap=640`, which stays logical in the signature and is
  scaled at its use.

The test also asserts that it finds the helper at least as many times as the
implementation lands with: 86 call sites in widget code in the prototype, and 87 as landed,
since Change 4's tab inset adds one (`PX_FLOOR = 87`). That floor is written into the test
and only ever rises, so a refactor that hides sites from the walk cannot pass vacuously.

Without the guard, the next patch that adds `pady=(0, 4)` reintroduces a defect visible
only at 250 %, on a machine the CI never runs. The guard is narrower than "no unscaled
constants", and one residual is stated plainly: a literal first assigned to a variable or
an attribute and then passed, such as `padx=self.GAP`, is not caught. Review catches that, and the
geometry-method rule covers the arithmetic cases the inventory found [infer]. (Author's
call - see the end.)

## Change 6 - the layout harness at the real scales

`tools/check_gui_layout.py` gains an aware mode and scale-relative pins.

1. **Two runs, two processes.** Awareness is one-way per process [doc], so the battery runs
   the harness twice:
   - **plain** - unaware: factor 1.0, the 100 % case;
   - **`--aware`**.

   What `--aware` does:
   - It calls the window's own `_declare_dpi_awareness()` before the first root.
   - It asserts that the effective awareness (`_dpi_awareness()`) is 1 or 2, not that this
     call took, since a host that already declared awareness absorbs it (Change 1).
   - It asserts that the window's factor equals the process's own DPI over 96.
   - Where `_dpi_supported()` is false (a Tk outside the measured set, or not Windows), it
     prints `aware run not applicable: <reason>` and exits 0, so the battery reads as
     skipped, not failed.
   - Otherwise it ends with `aware run verified at factor <f>`. A2 needs that line with
     2.5, so a skip, or a run on a 100 % display, which is the plain run again at factor
     1.0, cannot pass for the 250 % verification.

   The plain run asserts its own premise the same way. If `_dpi_awareness()` reports 1 or
   2, it stops with `plain run needs an unaware process; this one reports <n>`. An
   already-aware launcher then reads as an environment problem, not as a broken layout. An
   unreadable awareness (`None`, as off Windows) is not a declared one, and the run
   proceeds.

   The plain run on a high-DPI machine is a faithful 100 % run: an unaware process is shown
   a 96-dpi world, with its fonts, theme parts and system metrics alike [meas], and the
   harness stubs the work area. The only difference from a real 100 % display is the
   1536x960 virtual screen, which caps simulated geometry larger than that. The harness uses
   none.
2. **Every pixel literal in the harness becomes `P(n)`,** the window's own `px` at its own
   factor. The exceptions are:
   - the notebook's 4 px of side border (two 2-px borders, unscaled at both DPIs [meas]);
   - the 3-px centring tolerance, an error bound rather than a size, since integer centring
     is off by at most a pixel at any factor [infer];
   - counts of lines, which the window turns into pixels through the font itself:
     `_sash_px(2)`, `_sash_px(7)`, `cget("height") == 7` and `lines < 7` (L514-587) stay as
     they are.

   The sites, at `47cec4b` (line numbers from `grep -n`, to be re-pinned at
   implementation), with the prototype's passing conversion for each [meas]:

   | Line | Today | At factor f |
   |---|---|---|
   | 231, 538, 600, 619 | default simulated work area 1536x912 | P(1536)xP(912): at 2.5, 3840x2280, the measured physical work area |
   | 232 | the stub `lambda root: WORK["area"]` | `lambda root, scale=1.0: WORK["area"]`, since `_work_area` gains the parameter; no other harness stubs it [doc, grep] |
   | 278-280 | opens at 940x640 | P(940)xP(640): at 2.5, 2350x1600, the measured on-screen size today |
   | 281 | tab inner width 916 | P(940) - 2*P(10) - 4 |
   | 283 | filter frame 896 | P(940) - 4*P(10) - 4 |
   | 382, 462, 486 | audits at 940x640, the fit floor, 871x432 | P(940)xP(640), P(760)xP(420), P(871)xP(432) |
   | 490-498 | 683x384 -> opens and minsize 643x336 | P(683)xP(384) -> (P(683) - P(40), P(384) - P(48)) |
   | 528-534 | 911x480 -> opens 871x432, minsize 760x420 | P(911)xP(480) -> (P(911) - P(40), P(480) - P(48)), minsize P(760)xP(420) |
   | 565 | sash drag +60 | +P(60) |
   | 578, 590 | shrink to `"940x%d" % gp.FIT_FLOOR[1]` (940x420), re-enlarge to 940x640 | P(940)xP(FIT_FLOOR[1]), P(940)xP(640) |
   | 604 | scroll region 800x4000 | P(800)xP(4000) |
   | 409-410 | no label carries `wraplength` 880 | unchanged: it pins the old constant's absence |

3. **Fix the latent crash** the aware run exposed. The "no 880 constant" check calls
   `int(lbl.cget("wraplength"))`, and a ttk label whose wrap was never fitted reads back
   the ttk default, an empty string in Tk 9.0.4 [meas: `ttk.Label().cget("wraplength")`
   is `''`, and the aware no-factor run crashed with `invalid literal for int() ... ''`].
   It becomes `int(... or 0)`.
4. **New pins:**
   - (a) in the plain run the factor is 1.0 even on a high-DPI display - the
     no-double-scaling property;
   - (b) in the aware run the opening client size is P(940)xP(640) when the work area
     allows;
   - (c) in the aware run every named font the window's widgets use is point-sized (positive
     size) - the precondition for fonts scaling by themselves;
   - (d) in the aware run with factor > 1 on the vista theme, the first tab's label starts
     at least P(4) - 1 px from the notebook's left edge and from its top edge. That is read
     with `Notebook.identify`, scanning x along the tab strip's vertical midpoint and y
     along the label's horizontal midpoint. Measured at 2.5: (4, 4) with the theme's
     default (fails), (10, 10) with Change 4 (passes) [meas];
   - (e) in the plain run on the vista theme, the tab label's own inset measures
     `VISTA_TAB_INSET` on both axes, read the same way. A theme or Tk change that moves
     the inset then fails here, instead of leaving a stale named exception in Change 5
     [meas: (4, 4) at 1.0].
5. **Smoke:** run the other `tools/check_gui_*.py` harnesses once in an aware process.
   - They assert behaviour, not geometry: which engine calls run, in what order, under which
     guards. None carries a pixel literal in an assertion [doc, grep], so the factor cannot
     fail them spuriously, and a failure there is a release blocker: a code path broke
     under awareness.
   - An aware run only proves no code path breaks under awareness, which is cheap to show.
   - Their usual unaware runs are unchanged, factor 1.0 [infer, from Change 2's measured 96
     -> 1.0].

**Not adopted: simulating other scales by setting `tk scaling` and a stubbed factor.** It
was tried and measured unfaithful in both directions (see "The prototype"). A harness mode
that fails or passes for reasons a real display would not have is worse than none. The pure
size functions are different. They involve no Tk, so the unit tests can call them at 1.25,
1.5 or any factor and the arithmetic is exactly what a real display would get.

## Acceptance

| # | Criterion | How | Status |
|---|---|---|---|
| A1 | The 100 % layout is unchanged | plain harness run: every existing pin, unchanged numbers, plus the new items | measurable here |
| A2 | Crisp at 250 % at today's size | `--aware` harness run on the operator's display: green, ending `aware run verified at factor 2.5`, opening client 2350x1600. The harness stubs the work area at P(1536)xP(912) = 3840x2280, which is the measured real one; A4 is the unstubbed check | measurable here; the prototype passed [meas] |
| A3 | Text and widgets render without bitmap stretching | before/after screenshots of the harness's fake-cast fixture at 250 %, kept hub-side; the tab strip checked against Change 4 | measurable here; the prototype passed [meas, visual] |
| A4 | The installed window is aware after release | an outside per-monitor-aware observer reads the real window process: effective awareness 1, client 2350x1600 physical. The window plans at open and is closed unpressed. An unpressed open writes nothing: the only write outside an action button is the preference file, which changes on a destination pick (L3235) or after an Apply into a re-rooted destination (L312) [doc] | hub-side measurement after install |
| A5 | 150 % | the `--aware` run and a screenshot in a real 150 % session, with the precondition probe before signing out (D4's four steps) | required before release, run by the maintainer (D4, ruled A) |
| A6 | Tk 8.6 | not supported, so unchanged from today, and the aware run reports itself not applicable | settled (D3, ruled A) |
| A7 | No code path breaks under awareness | the other GUI harnesses, once each, in an aware process | measurable here |
| A8 | The guards hold | the pure tests, the Change 5 source guard and its `main()`-order rule included, pass in CI on every leg | runs in CI |

The audit's criteria were 100 %, 150 % and 250 %, the same size at 250 %, and crisp text.
A1-A3 cover three of them. A5 is the one this machine cannot provide.

## What system awareness does not fix, and what it costs (residuals)

- **A secondary monitor at a lower scale than the primary - a regression for that
  configuration, decision D1.** Take a 250 % panel as the primary at sign-in, with a 100 %
  external monitor beside it.
  - Today the unaware window is drawn at 96 dpi, which is that monitor's own DPI, so there
    it is crisp at 1:1 [infer, from Microsoft Learn's description of unaware windows].
  - After the change the window is drawn at 240 dpi, and Windows downscales it on the 100 %
    monitor: soft where it was crisp [doc, Microsoft Learn: a system-aware window is
    bitmap-scaled on any display whose DPI differs from the system DPI].
  - Neither moving the window nor restarting it changes this, since the system DPI is fixed
    at sign-in. Only per-monitor awareness would, and that is a non-goal.
  - One monitor was connected at measurement. Whether the operator docks to a lower-scale
    external monitor is not known, and D1 accepted the trade either way.
- **A secondary monitor at a higher scale than the primary.** It is stretched (soft), as
  today on every high-DPI monitor [doc].
- **Signed in on one primary, using another.** For example, signed in docked to a 100 %
  primary, then undocked to the 250 % panel. Then the system DPI is 96, the factor is 1.0,
  and the window is crisp on the 100 % monitor and stretched on the panel, exactly as
  today, until the next sign-out [doc, Microsoft Learn; infer for the factor].
- **A host that makes the process aware on an unsupported Tk** (outside the measured set).
  The window does nothing and keeps factor 1.0, so its fonts are realized at the real DPI
  over unscaled pixel constants: the broken state. That is exactly what 0.16.5 does on
  such a host today, and this change neither causes nor fixes it [infer].
- **A theme other than vista on a supported Tk.** The window never chooses a theme, and Tk
  on Windows runs vista [doc: the source never calls `theme_use`; meas: `vista` in both
  modes]. If another theme were in force, the layout would still scale and only the tab
  inset would keep that theme's own, unmeasured value. Change 4 and pins (d) and (e) apply
  to vista only.
- **A scale change without signing out.** The system DPI is the primary display's at
  sign-in [doc], so the window is stretched (soft) until the next sign-out, never mis-sized.
  That rests on the precondition in Change 2 [infer], which D4's session probes.
- **The work-area clamp reads the primary monitor's work area** (`SPI_GETWORKAREA`), so a
  window Windows places on a smaller secondary monitor can exceed that monitor. This is
  inherited from 0.16.0 and unchanged here [doc, `_work_area`].
- **The stage-1 dialog's 100-character list**, in Consolas 9, is wider than a 1366-px panel
  at 200 % [infer]. That is true today as well: about 704 logical px, against 683.
  Pre-existing and unchanged by this spec; a follow-up for the dialog, not for DPI.
- **Windows' text-size setting** (Accessibility > Text size) is still not followed. F8's
  factor addresses DPI only; the audit's F13 gap stands.
- **Theme hairlines** (`-tabmargins`, the entry's inner padding, the Text panes' Tk-default
  padding) stay 1-2 physical px (Changes 3 and 4).
- **Font size and pixel factor disagree by 0.1 %.** Tk's font scaling comes from the
  millimetre width (3.3366 against an exact 3.3333 at 240 dpi), so fonts are 0.1 % larger
  than the pixel factor implies. Immaterial [meas].
- **The window icon.** The embedded 32x32 PNG (the non-installed fallback, L4564) may be
  upscaled by Windows for a 250 % title bar [infer]. That is unchanged in kind from today
  and out of scope.
- **Every setup other than Windows with Tk 9.0 is untouched:** `_dpi_supported()` is false,
  factor 1.0. Tk on macOS is believed to report 72 px/in [infer, not measured], which would
  have made the audit's `winfo_fpixels("1i") / 96` a 0.75 factor there.

## Cross-cutting

- **README.**
  - A new entry in its release story for the release that ships this, saying what shipped
    and what it costs on a lower-scale second monitor. The 0.16.0 entry's sentence ("DPI
    awareness ... is its own later release") stays as history; it is not rewritten.
  - The window section gains one sentence: crisp at the display's scale on Windows with
    Tk 9.0 (Python 3.14 as measured); on a second monitor at another scale Windows scales it
    and it is soft there, including on a lower-scale monitor where the old window was
    crisp.
- **The polish spec's non-goal** ("DPI awareness. Its own spec, ...") gets a dated pointer
  to this spec at implementation, the way earlier notes were added in place.
- **Version: 0.16.6** (D6, ruled). The window draws differently and does
  nothing differently. That is the polish spec's own line between a patch and a minor: it
  went to 0.16.0, "not a patch: two of the changes alter what the window does before and
  between engine calls".
- **Sequencing for the implementer.** Each commit leaves the plain harness green and the
  shipped window drawing as today until the last:
  1. Change 1's three functions and Change 2's factor, with their unit tests. Nothing calls
     them from `main()` yet.
  2. Change 3's sites (plain harness identical, by construction and by run).
  3. Change 5's source guard, without its `main()`-order rule.
  4. Change 4's tab style and Change 6's aware mode: `--aware` is green from here, since it
     declares awareness itself.
  5. Change 1's call in `main()`, and Change 5's `main()`-order rule with it. This is the
     one-line switch, last and reversible.
- **Hub-side** (not this repo): the audit note's F8 gets a status line when this ships.
  The probes' raw measurements and the screenshots stay in the hub.

## Tests

Pure-function coverage (no tkinter):

1. `test_window_geometry_clamps_to_the_work_area` (existing): every current assertion
   unchanged at `scale=1.0`. New cases:
   - at 2.5 on (3840, 2280): (2350, 1600) and minsize (1900, 1050);
   - at 2.5 on (1708, 960): both (1608, 840);
   - at 1.5 on (1366, 720): (1306, 648) and minsize (1140, 630);
   - for any work area of at least `(px(280), px(288))`, the result never exceeds the area;
   - below that, the `_ABSOLUTE_MIN` floor wins as it does today, now `px(240)`.
2. `test_scale_factor`: with `ctypes` and the root stubbed,
   - 96 -> 1.0, 120 -> 1.25, 144 -> 1.5, 240 -> 2.5, and 72 -> 0.75 (no floor);
   - `GetDpiForSystem` missing or returning 0 -> the `GetDeviceCaps` value, with the DC
     released;
   - both failing -> `winfo_pixels("1i")`;
   - unsupported (not Windows, or a Tk outside the measured set) -> 1.0 with no ctypes
     call.
3. `test_dpi_supported_and_declare`:
   - `_dpi_supported()` is false off Windows, with `tk` set to `None`, and for a `TkVersion`
     outside the measured set (8.6 and 9.1 both tested);
   - `_declare_dpi_awareness()` makes no call when unsupported;
   - `OSError` is absorbed and nothing raises.
4. `test_px_is_identity_at_one`: for every logical value in the inventory, `px` at 1.0
   returns it unchanged.
5. `tests/test_gui_dpi.py` (Change 5): the source guard, its geometry-method rule, its
   found-count floor, and `main()` calling `_declare_dpi_awareness()` before `tk.Tk()`.

Harness coverage (`tools/check_gui_layout.py`):

6. The plain run: its awareness premise, every existing check with its numbers unchanged,
   and new pins (a) and (e).
7. The `--aware` run: the awareness assertions of Change 6 item 1, every check at the
   factor, and new pins (b), (c) and (d).
8. The aware smoke of the other GUI harnesses (A7).

## Decisions taken (maintainer, 2026-11-02)

The maintainer ruled on all six on 2026-11-02: "D1 A, D2 A, D3 A, D4 A, D5 yes, D6
0.16.6". Each ruling is the maintainer's own call, given explicitly, and each matches the
default this spec recommended. Every decision below keeps its options and the evidence
they were weighed on, so that the options not taken stay on record. They are in
dependency order.

**D1. Accept that a lower-scale second monitor gets softer - the go/no-go.**
**Ruling: Option A, accept it (the maintainer's call). Not taken: B, the opt-out switch;
C, waiting for per-monitor awareness.**
System awareness makes the window crisp on the display it was signed in on. It gets
softer on a secondary monitor whose scale is *lower* than the primary's, where today's
unaware window happens to be crisp (residuals, first bullet).

- Option A (recommended): accept it. The measured setup is one 250 % display, where every
  window today is soft. The cost lands only on a mixed setup. There the window is still
  correctly sized and readable, just scaled.
- Option B: accept it, and add an opt-out environment switch that skips the awareness call
  (for example `CCS_GUI_DPI=off`). That is a few lines and one more documented knob.
- Option C: hold the change for per-monitor awareness. That is a larger design: re-layout
  on `WM_DPICHANGED`, which Tk would have to drive and which no probe here has measured.

Whether the operator docks to a lower-scale monitor was the fact that would decide between
A and B. The ruling accepts the trade without it, so no opt-out switch is built.

**D2. Where the factor comes from - a correction to the audit.**
**Ruling: Option A, the process's own DPI (the maintainer's call). Not taken: B, the
audit's `winfo_fpixels` formula.**
The audit proposed `winfo_fpixels("1i") / 96`. Measured, that is 1.00098 at 100 % and
2.5025 at 250 %, because Tk derives DPI from a millimetre-rounded screen width. It would
turn 940 into 941 at 100 %, moving every pin, and give 2352 rather than 2350 at 250 %.

- Option A (recommended): the process's own DPI. That means `GetDpiForSystem() / 96`, then
  `GetDeviceCaps`, then Tk's rounded `winfo_pixels("1i")` as the source that cannot fail
  (Change 2). Measured exact (1.0, 2.5), and self-consistent with Windows' stretching:
  unaware processes are told 96.
- Option B: the audit's formula as written. Rejected on the measurement.

**D3. Which Tk versions get any of this.**
**Ruling: Option A, only the measured Tk 9.0 (the maintainer's call). Not taken: B, every
Windows Tk; C, measuring Tk 8.6 first. C stays available as the route by which the
supported set could widen later.**
Tk 9.0.4 (Python 3.14) is measured: point-sized named fonts, point-sized theme paddings,
the prototype green. Windows users on Python 3.9-3.13 get Tk 8.6 [infer], whose named-font
and theme-padding units are [open]. One reviewer (Gemini, round 1) reports that Tk 8.6's
vista theme sizes the button and checkbutton paddings in pixels. That is unverified here,
and it is part of what is [open]. The package
supports Python 3.9 and later [doc, `pyproject.toml`], so the gate decides who benefits:
every Windows install on Python 3.9-3.13 stays as today [infer], and the operator's
install, on 3.14, gets the change.

- Option A (recommended): support only the measured version, Tk 9.0 (`_dpi_supported()`).
  Anything else behaves exactly as 0.16.5, whatever the host set. A later Tk 9.x joins the
  set after the probes and both harness runs are repeated on it. Pro: no unmeasured
  toolkit changes, now or when a future Python moves to a newer Tk. Con: Tk 8.6 users stay
  blurry, and so does a future Tk until someone re-measures.
- Option B: support every Windows Tk. Pro: everyone crisp, if Tk 8.6 behaves like Tk 9.
  Con: unmeasured; a font or padding regression on a public package.
- Option C: measure Tk 8.6 first. This needs a Python 3.13 interpreter on this machine
  (none is installed; installing one is a download that needs the maintainer's yes), then
  the probes re-run. It turns B into a measured choice. A can ship meanwhile, and C widens
  it later.

**D4. The 150 % criterion.**
**Ruling: Option A, run the real 150 % session once before release (the maintainer's
call). Not taken: B, waiving it; C, deferring it. A5 is therefore a release gate: the
maintainer runs the four steps below, since they change a display setting and sign out.**
This machine cannot simulate 150 % faithfully (measured, above). A real check is:

1. Set the primary display to 150 %.
2. *Before* signing out, run the interpreter probe once in a system-aware process. That
   tests Change 2's precondition in the scale-changed, not-signed-out state, and costs
   nothing extra.
3. Sign out and in, run the `--aware` harness and take a screenshot.
4. Set the display back to 250 %, and sign out and in.

Why it matters more than it might seem: at 150 % the default font's line height is 25 px
against a linear 22.5, a 1.11 ratio. At 250 % it is 1.09. So 150 % is the tighter
vertical case [meas: a font's line height at a given pixel size, which survived the failed
simulation; see "The prototype"], and it is a common laptop setting.

What it tests is the ordinary 150 % user, one whose display was 150 % at sign-in. A scale
change without signing out is a different, known-soft state (residuals) that no test
covers.

- Option A (recommended): run it once before release, about ten minutes and two
  sign-outs.
- Option B: waive it. Ship on A1-A4 and A7, and record 150 % as untested in the release
  note.
- Option C: defer to the first real 150 % display that turns up.

**D5. Ship the tab-inset style line (Change 4)?**
**Ruling: yes (the maintainer's call, matching the default). Not taken: shipping without
it.**
It is the one visible defect the prototype's screenshots showed, its fix is one style call
gated on factor > 1 and the vista theme, and it was measured on both axes.

**D6. Version.**
**Ruling: 0.16.6 (the maintainer's call, matching the default). Not taken: 0.17.0.**
The window draws differently and does nothing differently, which is the polish spec's own
patch/minor line. 0.17.0 is the alternative if the maintainer wants the visible change
marked.

## Calls made by the author (check these while reading)

- **System awareness, not per-monitor**, carried from the audit (Change 1). What that costs
  is D1.
- **One support predicate gates everything**, so an unsupported setup is exactly 0.16.5
  (Change 1).
- **Tk's `winfo_pixels("1i")` is the factor's last source, and the factor has no floor**,
  so an aware process never gets a factor that disagrees with its fonts (Change 2).
- **Logical pixels in source, scaled at the Tk boundary**, with `scale` threaded through
  the pure size functions. That lets the unit tests call them at any factor in one process.
  Windows at different factors still need separate processes (Change 6).
- **Every padding the source sets is scaled**, borders included (Change 3). The only
  exceptions are named: Tk's own defaults, the drag tolerance, and `VISTA_TAB_INSET`. That
  is what lets Change 5 guard the rest.
- **`WINDOW_CHROME` is scaled rather than measured** (Change 3).
- **The tab inset is symmetric** and gated on the vista theme (Change 4).
- **`TNotebook -tabmargins` and `TEntry -padding` are left as the theme sets them**
  (Change 4).
- **The source guard (Change 5)** is part of the change, not a follow-up, and checks
  `main()`'s call order.
- **No simulation mode** in the harness (Change 6).
- **The screenshots stay hub-side**, even though they show only the fake cast. The public
  repo carries no binary evidence for its other GUI specs either.
- **The post-release check of the real window (A4) is a hub-side measurement**, not a repo
  tool: it opens the real window on a real store.

## Non-goals, stated so the review can disagree with them

- **Per-monitor (v2) awareness.** It would make the window crisp on every monitor at its
  own scale, at the price of re-laying it out on `WM_DPICHANGED`, a code path Tk would have
  to drive and that this spec does not measure. System awareness fixes the measured case
  (one display). On a higher-scale second monitor it is as soft as today; on a lower-scale
  one it is softer than today (D1).
- **Following Windows' text-size setting.** An accessibility gap the audit recorded (F13).
  Separate in kind: it is a user preference, not a DPI.
- **macOS and Linux HiDPI.** No call, factor 1.0, nothing changes. Tk on those platforms
  has its own scaling story, not measured here.
- **Changing any font size, or reflowing the layout for 250 %.** The goal is today's
  layout, crisp. The prototype shows the existing layout survives the real 250 % scale.
- **A manifest.** The package does not own the executable that creates the window.

## Review (revisions record)

**Round 1, 2026-11-02.** Panel:

- Codex reported;
- Gemini (agy) reported;
- the repo-aware Fireworks.ai roster: Kimi reported, and so did DeepSeek, its run entering
  synthesis after an exploration-clock abort.

All four canaries matched; DeepSeek's and Kimi's are the roster's own token check.
Secret-file check: nothing listed. The roster read the window source, the layout harness,
the README, the tests and the polish spec; their code checks are folded in below.

**Applied:**

- **[DeepSeek] The second-monitor residual was backwards for a lower-scale monitor.**
  Today's unaware window is crisp at 1:1 on a 100 % external monitor, and a system-aware
  one is downscaled there. The residual now says so, the README sentence says so, and
  accepting that trade is now decision D1, first in dependency order.
- **[Codex] Awareness taken plus every DPI read failing would have given factor 1.0,** the
  prototype's broken state. Change 2's last source is now Tk's own `winfo_pixels("1i")`,
  which cannot fail, and the anti-goals name that state.
- **[Codex+agy+DeepSeek] One initialization contract.**
  - A single support predicate now gates the call, the factor and the tab style, so an
    unsupported Tk is exactly 0.16.5 even if the host made the process aware.
  - The effective awareness is read rather than assumed.
  - `--aware` asserts awareness 1 or 2 instead of "the call took", and reports itself not
    applicable below Tk 9.
- **[agy] Nothing checked that `main()` makes the call before `tk.Tk()`.** The harness
  declares awareness itself, so a missing production call would have passed every test.
  Change 5 now checks `main()`'s order.
- **[Codex+agy] Change 4's `px(4) - 4` broke Change 5's own rule.** The inset is now the
  named physical constant `VISTA_TAB_INSET`, allowed by name. "No exceptions" now reads
  "no exceptions among the source's own constants", with the true exceptions listed.
- **[agy+DeepSeek] The guard missed the sash and holds arithmetic** (literals beside local
  names). Change 5 gains a rule for the geometry methods.
- **[agy+DeepSeek] `_work_area` and the unit contract.** Geometry is in the process's own
  pixels. `_work_area(root, scale)` scales its non-Windows margin, its docstring changes,
  and `work` is never scaled.
- **[agy+Kimi] The harness conversion table missed L578, L590 and L604.** It is now a rule,
  with every site listed by line and the two exceptions named. The prototype had already
  converted the first two.
- **[Kimi] Change 4's vertical half was unpinned.** The inset was measured as 4 px on both
  axes. The padding is now symmetric, and pin (d) checks both axes with stated
  coordinates.
- **[DeepSeek] Change 4 assumed additive padding.** Now measured: the label's first pixel
  moved from (4, 4) to (10, 10).
- **[agy+DeepSeek] `GetDpiForSystem` returns 0 rather than raising, and the screen DC must
  be released.** Change 2 treats zero as no answer and releases the DC.
- **[Codex+agy] The clamp's boundary.** The precedence is stated (the `_ABSOLUTE_MIN` floor
  wins, as in 0.16.0), and Test 1 and the anti-goal are qualified to match.
- **[Codex+agy] Sequencing.** The helpers land first, the aware harness goes green at step
  4, and only the `main()` call is last.
- **[All] 85 against 86.** Reconciled: 85 literals rewritten, plus `px(wrap)`, is 86 helper
  calls, and the guard's floor is that count.
- **[DeepSeek] Tk's own pixel defaults and the drag tolerance** are listed as deliberate
  exceptions.
- **[agy] The "windows at different factors in one process" sentence** is corrected: the
  unit tests call the size functions at any factor, while windows need separate processes.
- **[Kimi] Why pure-function 150 % tests are faithful** while harness simulation is not is
  stated.
- **[Kimi] The README's 0.16.0 sentence** stays as history, and a new entry is added
  rather than rewriting it.
- **[DeepSeek+Kimi] D4's 1.11 basis** is explained: a font metric at a pixel size, which
  survived the failed layout simulation.
- **[DeepSeek] A2's 2350x1600 is stub-derived.** A2 now says so, and names the stub value
  as the measured real work area.
- **[Codex] A smoke of the other GUI harnesses under awareness** is added (A7).
- **[DeepSeek] D3's blast radius** is now stated: every Windows install on Python 3.9-3.13
  stays as today.

**Rejected, with reasons:**

- **[Kimi] "Scaled chrome makes the same-size goal an accident of one display."** Today's
  stretched window is `f x min(940, work/f - 40)`, which equals the scaled clamp
  `min(P(940), work - P(40))` on any display. The measured chrome does not enter either
  formula. The identity is now in the Goals.
- **[Kimi] "`OleDLL` needs an explicit `restype` for the `OSError` guard."** Measured: its
  default `restype` is `ctypes.HRESULT`, and a second call raised `OSError` 0x80070005.
  IDLE's own call sets none.
- **[Kimi] "`_Dialog` gains a dependency on the app."** It already takes the app as its
  first argument (L1663).
- **[Kimi] "D4's sign-out test checks the uncommon path."** A 150 % laptop user is signed
  in at 150 %, which is what the test reproduces. The scale-changer is the known-soft
  residual. D4 now says this.
- **[DeepSeek] "The empty `wraplength` mechanism is unsupported."** Measured:
  `ttk.Label().cget("wraplength")` is `''` in Tk 9.0.4, and the aware no-factor run's
  traceback names it.
- **[Codex] "Check the other harnesses with long-content and failure fixtures under
  awareness."** Narrowed to the A7 smoke. The layout harness's fixture already carries the
  long cases (a 130-character title, a 78-row plan, the two-button banner, the stage-1
  list), and its dialog pins run in the aware run. The other harnesses assert engine-call
  behaviour, which no pixel touches.

**Round 2, same sitting.** Panel, all four on the round-2+ addendum:

- Codex reported `Blockers: none` and one major;
- Gemini (agy) reported `Blockers: none`;
- the repo-aware Fireworks.ai roster: DeepSeek reported, its run again entering synthesis
  after an exploration-clock abort; Kimi reported, after hitting its 12-iteration loop cap.

Every canary matched. Kimi's synthesis turn ran on about 3,000 reasoning tokens, and its
top finding was fabricated (below).

**Applied:**

- **[Codex+DeepSeek, agy as a next step] The round-1 guard rule was defective.** It named
  "the dialog builders" and `min`/`max` arguments, so it would have rejected the stage-1
  list's `min(len(mappings), 14)`, a line count.
  - The rule is now scoped to the four methods whose arithmetic the inventory found.
  - Character and line counts are a named exception class.
  - "`default=`" is defined as the keyword argument, not a function's parameter default.
- **[DeepSeek] `max(1.0, ...)` contradicted "an aware process never gets 1.0"** for a DPI
  below 96. The floor is removed, the factor is the DPI over 96 whatever it is, and Test 2
  gains 72 -> 0.75.
- **[DeepSeek] The harness rule had an unnamed exception class:** line counts
  (`_sash_px(2)`, `_sash_px(7)`, the height of 7). Now named.
- **[DeepSeek] The `_work_area` stub's signature** must take the new parameter. It is now in
  the site table, and no other harness stubs it.
- **[DeepSeek] The plain run's unaware premise was unguarded.** It now stops with a named
  environment message when its process is already aware.
- **[DeepSeek+Kimi] The awareness-2 residual overclaimed.** Under a host's per-monitor
  awareness the factor is the system DPI, so a window opening on a monitor of another DPI
  is mis-sized there, dialogs included. Reworded.
- **[Kimi] `VISTA_TAB_INSET`'s provenance.** Its comment carries the measurement's date and
  Tk version, and a new plain-run pin (e) re-measures it against the theme, so the named
  exception cannot go stale silently.
- **[Kimi] The pure tests, the source guard included, are an acceptance gate (A8)** - the
  one check that runs in CI.
- **[Kimi] A4's "unpressed open writes nothing"** now names the only write outside an
  action button: the preference file, on a destination pick or after an Apply into a
  re-rooted destination.
- **[Kimi] D3's Tk 8.6 padding claim** is marked [open] and attributed, rather than given a
  basis mark of its own.
- **[Kimi] A host that makes the process aware on an unsupported Tk** is now a residual: the
  same state as 0.16.5 on that host, neither caused nor fixed here. The anti-goal is scoped
  to what this change does.
- **[Kimi] The harness line numbers** are marked as `grep -n` output, to be re-pinned at
  implementation.

**Round 3, same sitting - the cap round.** Panel, all four on the round-2+ addendum:

- Codex, Gemini (agy) and Kimi each reported `Blockers: none`. Kimi read the whole window
  source and the Level harness, and confirmed each round-2 fix against them.
- DeepSeek reported `Blockers: none` with four majors. Its run again entered synthesis after
  an exploration-clock abort.

Every canary matched.

**Applied** (review-driven but not panel-verified, since the cap ends the loop; the
implementing session should read these sections most skeptically):

- **[DeepSeek] The round-2 residual named "a theme other than vista" as unsupported,**
  which `_dpi_supported()` never checks: a defective round-2 reword. The unsupported-host
  residual is now version-only. A separate residual says a non-vista theme would scale
  fully and keep only its own tab inset, and that the window never chooses a theme.
- **[DeepSeek] The plain run would have stopped on an unreadable awareness** (`None`, as
  off Windows). It now stops only on a declared awareness of 1 or 2.
- **[DeepSeek] The "1.0 exactly when Windows stretches" identity had an unstated
  precondition:** the fonts and the factor reading the same DPI after a scale change
  without signing out. Change 2 states it, quotes Microsoft's virtualization sentence, and
  marks its application to Tk's calls as [infer]. D4's session probes it before signing
  out.
- **[DeepSeek] `>= 9.0` admitted unmeasured Tk versions,** against D3's own reason. The
  predicate is now a set of measured versions, today `{9.0}`, widened only after a
  re-measure. Test 3 checks 8.6 and 9.1.
- **[DeepSeek] The aware run could not tell "verified at 250 %" from a skip or a 100 %
  run.** It now ends `aware run verified at factor <f>`, and A2 needs that line with 2.5.
- **[DeepSeek] Smaller points:**
  - the smoke's premise is marked (no pixel literal in the other harnesses' assertions, by
    grep), and its failure is defined as a release blocker;
  - the helper has one spelling, `_px(n, scale)`, with `SyncApp.px` calling it;
  - the guard covers the module's size functions too, and says plainly that
    `padx=self.GAP` passes;
  - the L578 site is described as the harness writes it.
- **[Kimi, noted] Tk's rounded `winfo_pixels` could sit one off the OS DPI on some panels.**
  Change 2 says so, and that the source is reached only after both OS reads fail.

**Rejected:**

- **[DeepSeek, from recall] "IDLE uses `windll` with a broad `except Exception`".** The file
  on disk (CPython 3.14, `Lib/idlelib/util.py`) calls `ctypes.OleDLL('shcore')` and catches
  `(ImportError, AttributeError, OSError)`, as Change 1 says.

**Loop closed at the 3-round cap.** In every round all four engines reported; none failed.

**Rejected, with reasons:**

- **[Kimi, as a blocker] "The spec's central mechanism cites a `COMFY_COUNT` knob" in a
  section called "The one knob", plus `DPI_DISCOVERY` and `SPEC_INSET`.** None of those
  names appears in this spec or in the window source: `grep -c` finds zero matches in
  both. The finding describes a different document. Its other findings were answerable on
  their merits and are applied above.

## Appendix - the probes

All were run on 2026-11-02 on the operator's machine, from a scratch directory outside the
repo; none is committed.

- **Interpreter probe.** It ran under the installed venv's `pythonw.exe`, once unaware,
  once after `SetProcessDpiAwareness(1)`, and once more with IDLE's `fix_scaling` applied
  (identical to the second, because no named font is pixel-sized). It reported:
  - awareness;
  - system metrics and the work area;
  - every monitor's rectangle and DPI;
  - Tk's patchlevel, `tk scaling`, `winfo_fpixels`, the screen size and the theme;
  - every named font's configured size and line height;
  - the requested sizes of a ttk Button, Checkbutton, Entry, Scrollbar, Label, Notebook
    and Text.

  It built no `SyncApp` and read no store.
- **Launch probe.** It started the installed `ccs-gui.exe` and waited for the "Claude
  sessions" window. From a per-monitor-aware observer it read the window process's image,
  awareness, `GetDpiForWindow`, window, frame and client rectangles, and the process chain.
  It then posted `WM_CLOSE`, the idle close path, with nothing dirty and nothing pressed.
  The window's own open-time reads were the only store access.
- **Pixel scan.** An AST walk of the window source. It produced the inventory in Change 3:
  59 non-zero literals and 23 `PAD` uses in pixel arguments, 4 in geometry arithmetic, 9
  constant values in 6 module names, and 7 character or line units, with one counter false
  positive set aside.
- **Prototype and harness copy.** The source was rewritten mechanically from the scan's
  positions, plus the factor helpers and the `main()` call, and the harness copy's pins
  were re-expressed by Change 6's rule. Both were run unaware and aware, and with the
  Change 4 tab style, first with an asymmetric and then with the symmetric inset.
- **Simulation attempts.** `tk scaling` set to f x 96 / 72 with the factor stubbed to f
  (f = 1.25, 1.5, 1.75, 2.0, 2.5), on an aware base and on an unaware base, with and
  without reconfiguring the named fonts. Every attempt was unfaithful, as recorded above.
- **Screenshots.** The harness fixture (fake cast only) was made visible and captured by
  physical client rectangle, unaware and aware, then cropped and zoomed side by side.
- **Font-refresh, style and tab probes.** They measured three things:
  - whether named fonts and theme parts follow a later `tk scaling` change (fonts only
    after reconfiguring, parts never);
  - the vista theme's notebook, button, entry and checkbutton paddings in both modes;
  - the tab label's first pixel on both axes, with and without the style padding.
- **ctypes probe.** It read `OleDLL`'s default `restype` and called
  `SetProcessDpiAwareness(1)` twice to see the second call's error, plus Tk 9.0.4's
  `ttk.Label` and `Text` defaults.
