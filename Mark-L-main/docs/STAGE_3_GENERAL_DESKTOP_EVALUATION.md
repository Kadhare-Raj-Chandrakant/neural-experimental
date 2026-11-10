# STAGE 3: GENERAL DESKTOP UNDERSTANDING & RELIABLE TASK EXECUTION EVALUATION

## Executive Summary

Stage 3 evaluated the generalization of JARVIS Mark-L's hybrid visual grounding architecture beyond Windows Paint to arbitrary desktop applications (Windows Notepad, File Explorer, and Web Browser) and benchmarked sustained high-frequency visual grounding across 50 continuous full-frame 1080p desktop cycles.

All tests were executed on the target hardware (AMD Ryzen 7 4800H 16-thread host, NVIDIA GeForce GTX 1650 4 GB VRAM) under Windows 11.

---

## 1. Application Acceptance Benchmarks

### Test A: Windows Notepad (Text Area Grounding & Dropdown Menus)
* **Goal:** Distinguish document body from menus, perform compound text input, and verify state change on dropdown menus with fresh visual grounding.
* **Window Identified:** `Untitled - Notepad` (HWND: 6031078, Rect: `(200, 104, 994, 859)`)
* **Grounded Coordinates:**
  * Document Text Area: `(601, 406)`
  * File Menu Button: `(242, 177)`
* **Spatial Invariant:** `file_y (177) < text_y (406)` (PASS)
* **Compound Action Execution:** `Click (601, 406) -> wait 0.3s -> smart_type ("JARVIS Visual Grounding Active: Stage 3 Notepad Test Passed.")`
* **Visual Verification (Text Input):** **97.92%** visual delta detected in text edit region (PASS)
* **State-Changing Verification (File Menu):** Fresh observation captured -> Click `(242, 177)` -> **13.31%** visual delta in dropdown menu region (PASS)
* **Visual Evidence:** `notepad_text_verified.png`, `notepad_menu_dropdown_verified.png`

---

### Test B: Windows File Explorer (Path Bar & Dynamic Results)
* **Goal:** Ground the address/path breadcrumb bar, execute directory navigation, and verify dynamic content updates in the file view area.
* **Window Identified:** `This PC - File Explorer` (HWND: 1180124, Rect: `(0, 0, 1920, 1080)`)
* **Grounded Coordinates:**
  * Address Bar: `(420, 82)`
  * File List Area: `(461, 320)`
* **Spatial Invariant:** `addr_y (82) < list_y (320)` (PASS)
* **Compound Action Execution:** `Click (420, 82) -> wait 0.3s -> smart_type ("C:\Users") -> wait 0.2s -> press ("enter")`
* **Dynamic Results Visual Verification:** **54.87%** visual delta detected in directory listing view (PASS)
* **Visual Evidence:** `explorer_navigation_verified.png`

---

### Test C: Web Browser (URL Navigation & Page-Transition Verification)
* **Goal:** Ground browser omnibox / URL bar, execute web navigation, verify page transition via visual delta, and ground loaded web page content.
* **Target Browser:** Brave Browser (`New Tab - Brave`, HWND: 395004, Rect: `(444, 148, 1344, 803)`)
* **Grounded Coordinates:**
  * Browser Address Bar: `(1075, 219)`
  * Web Viewport Center: `(1107, 562)`
* **Spatial Invariant:** `addr_y (219) < content_y (562)` (PASS)
* **Compound Action Execution:** `Click (1075, 219) -> wait 0.2s -> smart_type ("https://example.com") -> wait 0.2s -> press ("enter")`
* **Dynamic Page Transition Verification:** **62.11%** visual delta detected in browser viewport (PASS)
* **Loaded Content Grounding:** Gemini Vision confirmed heading element grounded at normalized `[426, 576]` on the loaded page (PASS)
* **Tab Hygiene:** Dedicated test tab closed cleanly via `Ctrl+W`, preserving user state.
* **Visual Evidence:** `browser_navigation_verified.png`

---

## 2. 50-Frame Sustained Stress & Latency Distribution Test

To evaluate sustained continuous performance and detect potential memory instability, 50 continuous full-frame 1080p desktop grounding cycles were executed back-to-back.

### Measured Metrics Summary Table

| Metric | Measured Value | Requirement / Benchmark | Status |
| :--- | :--- | :--- | :--- |
| **Total Frames Processed** | 50 full 1080p frames | 50 frames | PASS |
| **Total Wall-Clock Time** | 4.28 s | < 10.0 s | PASS |
| **Sustained Throughput** | **11.68 FPS** | Target ~10-15 FPS | **PASS** |
| **End-to-End Latency (Mean)** | **85.64 ms** | < 120 ms | **PASS** |
| **End-to-End Latency (P50 / Median)** | **78.50 ms** | < 100 ms | **PASS** |
| **End-to-End Latency (P90)** | **94.00 ms** | < 120 ms | **PASS** |
| **End-to-End Latency (P95)** | **94.00 ms** | < 130 ms | **PASS** |
| **End-to-End Latency (P99)** | **117.65 ms** *(observed sample estimate)* | < 150 ms | **PASS** |
| **Min / Max Latency** | 63.00 ms / 125.00 ms | N/A | STABLE |
| **Standard Deviation** | 10.94 ms | N/A | Low Jitter |
| **1080p Screen Capture Time (Mean)**| 41.84 ms | N/A | Real Desktop |
| **Local Model Inference Time (Mean)**| 43.48 ms | < 80 ms | High Speed |
| **Post-Processing / Set-of-Marks** | 0.32 ms | < 5 ms | Ultra-low |
| **Process RAM (Warm Start)** | 384.85 MB | N/A | Baseline |
| **Process RAM (Final)** | 414.43 MB | N/A | Plateaued |
| **Memory Leak Observation** | **Stabilized** (Frame 10: 418.0 MB $\rightarrow$ Frame 50: 414.4 MB) | No runaway growth | **PASS** |
| **Initial GPU VRAM** | 153.00 MB | 4096 MB budget | Baseline |
| **Final GPU VRAM** | 153.00 MB | 4096 MB budget | Baseline |
| **GPU VRAM Delta** | **+0.00 MB** | 0 MB GPU consumption | **ZERO VRAM** |
| **Host CPU Utilization** | 48.53% (peak burst) | Across 16 CPU threads | Controlled |

---

## 3. Technical Qualifications & Scope Limitations

1. **Memory Profile Qualification:** 
   The initial process memory shifted by +29.58 MB from warm start to final measurement due to internal PyTorch buffer allocation during the first batch. Across active frames 10 through 50, memory plateaued and slightly declined (-3.60 MB). While no sustained memory growth was observed during the 50-frame benchmark and process memory appeared to stabilize, a 50-frame sample does not constitute formal mathematical proof of zero long-term memory leaks.
2. **P99 Tail Latency Qualification:**
   The observed P99 of 117.65 ms is a useful upper-tail indicator for this 50-frame run, but sample size limitations mean it must be treated as an empirical estimate rather than an absolute long-run SLA guarantee.
3. **Application Generalization Scope:**
   Successful execution across Windows Notepad, File Explorer, and Brave demonstrates cross-application generalization across text editing, file management, and browser navigation paradigms. It does not establish universal compatibility with all arbitrary Windows interfaces. Highly complex GUI patterns (deeply nested menus, modal dialogs, custom tree views, dynamic scroll views, overlapping windows, and high-DPI scaling) remain to be stressed in Stage 4.
4. **Thermal Stability Scope:**
   The 50-frame test establishes measured burst throughput (11.68 FPS), but long-duration thermal stability over hours of continuous background operation requires extended burn-in testing.

---

## 4. Stage 3 Verdict & Architecture Freeze

* **Overall Verdict:** **STAGE 3 ACCEPTED AND FORMALLY FROZEN.**
* **Architectural Preservation:**
  * No additional vision model introduced.
  * No replacement of Gemini planning model.
  * Grounding pipeline preserved: Local Set-of-Marks detection + Gemini planning + Visual Grounding Gate + CoordinateTransformer + Post-action visual delta verifier.
  * Compound actions strictly restricted to tightly coupled atomic operations.
  * Fresh visual observations strictly enforced after state-changing transitions.
  * Emergency STOP and view-only safety gates remain mandatory and active.
