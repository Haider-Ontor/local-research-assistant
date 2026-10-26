"""Your own settings. Copy this file to local_settings.py (same folder) and edit it.

local_settings.py is listed in .gitignore, so it stays on your computer and is never uploaded.
Anything set there overrides the default of the same name in config.py. Save, then restart the app."""
from pathlib import Path

HOME = Path.home()

# Folder you drop PDFs into (subfolders are included).
PAPERS_DIR = HOME / "Documents" / "Research Papers"

# Optional: your own proposal/thesis draft. The newest .docx/.pdf with "proposal" in its name in this folder is built
# into the assistant and re-read whenever you save a new version. To pin one file instead, set PROPOSAL_FILE.
# PROPOSAL_DIR = HOME / "Documents" / "PhD Proposal"
# PROPOSAL_FILE = HOME / "Documents" / "PhD Proposal" / "My proposal.docx"

# Optional: your Zotero data folder, or USE_ZOTERO = False to ignore Zotero.
# ZOTERO_DIR = HOME / "Zotero"
# USE_ZOTERO = True

# Answer model file in the models folder (see README, "Setup on a new computer").
# CHAT_MODEL_NAME = "qwen3.5-9b-q4_k_m.gguf"

# A few lines about what you work on. Explain mode and study guides relate findings to it.
RESEARCH_CONTEXT = ("PhD project: <your topic> - <the materials, methods and instruments you use>. "
                    "Also works on <another topic>.")

# Correct textbook facts the model must follow when it explains things. A small local model gets some of these wrong on
# its own - add a line whenever you catch it. Example for RF/microwave dielectric sensing:
REFERENCE_NOTES = """
- Complex relative permittivity: eps_r* = eps' - j eps''. eps' (dielectric constant) and eps'' (loss factor) are DIMENSIONLESS. Absolute permittivity eps = eps_r * eps0, with eps0 = 8.854e-12 F/m; only absolute permittivity has units (F/m).
- Loss tangent tan(delta) = eps''/eps'. Effective conductivity sigma = omega * eps0 * eps'' (S/m).
- Debye model: eps* = eps_inf + (eps_s - eps_inf) / (1 + j omega tau). Cole-Cole model: eps* = eps_inf + (eps_s - eps_inf) / (1 + (j omega tau)^(1 - alpha)) + sigma_s / (j omega eps0). eps_inf and eps_s are dimensionless relative permittivities, tau is the relaxation time (s), alpha (0 to 1) broadens the relaxation, sigma_s is the static (DC) ionic conductivity (S/m).
- Pure water near 25 C: static permittivity about 78, Debye relaxation near 19-20 GHz (tau about 8.3 ps). Dissolved salts mainly raise eps'' at low frequencies through ionic conductivity.
- Open-ended coaxial probe: measures the REFLECTION coefficient (S11) at the probe aperture pressed against the sample; software converts S11 into complex permittivity using a probe model. Calibrated with open, short and a reference liquid (usually water); validated with another liquid (e.g. methanol). The sensing depth is small (sub-mm to a few mm) and grows with probe diameter; air gaps and sample roughness cause large errors.
- S-parameters: S11 = reflection at port 1, S21 = transmission from port 1 to port 2, usually given in dB.
- Resonator sensors (split-ring, microstrip, cavity): the material under test mainly SHIFTS the resonance frequency (through eps') and lowers the Q factor / deepens or broadens the dip (through eps'').
"""
