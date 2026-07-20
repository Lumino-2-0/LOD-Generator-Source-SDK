# Source Engine LOD Builder

<div align="center">

![Version](https://img.shields.io/badge/version-1.10-blue.svg)
![Python](https://img.shields.io/badge/python-3.8+-green.svg)
![License](https://img.shields.io/badge/license-MIT-orange.svg)
![Platform](https://img.shields.io/badge/platform-Windows-lightgrey.svg)

**Automatic LOD (Level of Detail) generator for Garry's Mod and Source Engine**

[French version of this document (README_FR.md)](README_FR.md)

</div>

---

## Description

Source Engine LOD Builder is a powerful tool designed to automate the creation of LOD (Level of Detail) models for Garry's Mod props and other games powered by the Source Engine. 

The tool parses your Hammer VMF map files or recursive model directories, decompiles the original .mdl files automatically, generates optimized low-poly versions using Blender's decimation algorithms, updates the QC compilation scripts with appropriate LOD thresholds, and recompiles them back into game-ready assets.

### Why use LODs?

- Better performance: Reduces polycounts for distant objects, significantly lowering GPU workload.
- Substantial FPS gains: Improves fluidity on crowded or complex maps, providing an estimated 30% to 60% FPS increase in high-density areas.
- Adaptive rendering: Smooth transitions between accuracy levels based on player camera distance.
- Preserved visual fidelity: Invisible differences at medium and long distances, keeping gameplay immersive and smooth.

---

## Features

### Parsing and Discovery

- VMF File Parsing: Auto-extracts all props referenced in your Hammer editor map (.vmf) and counts their occurrences.
- Recursive Directory Scan: Scans designated local directories to find and inventory available loose .mdl assets.
- Automatic VPK Extraction: Searches and extracts original models and material dependencies straight out of Garry's Mod VPK archives.
- Precise Indicators: Displays vital statistics such as prop count, usage frequency, physics settings, and file sizes.

### Optimized Generation Pipeline

- All-in-one automation: Handles everything from the raw .mdl decompilation to the final compiled model package in a single click.
- Blender Integration: Automates Blender via Python scripts to apply proportional decimation, preserving original visual silhouettes.
- Customizable LOD Levels: Create up to 8 distinct custom levels of detail with tailored distances and decimation ratios.
- Physics Preservations: Advanced options to reuse the original collision model or generate a simplified physics mesh.
- Multithreaded Processing: Processes multiple assets in parallel, dramatically accelerating batch conversion workflows.

### Ergonomics and UI

- Drag and Drop Support: Instantly drag any .vmf file directly onto the interface to start compiling.
- Integrated 3D Preview: Embedded OpenGL & Pyglet viewer allowing real-time inspection of original meshes and generated LODs side-by-side.
- Filters and Sorting:
  - Filter by processing status (Ready, Processing, Done, Error).
  - Search by file size range (in KB) fully integrated into the UI.
  - Sort by usage frequency or file size.
  - Dynamic text search filter.
- Dual-Language Support: Fully localized French and English user interface.

---

## Installation and Usage

### Quick Setup: Pre-Compiled Release (For End-Users)

The tool is pre-packaged as a standalone Windows executable. No external Python environment is needed.

1. Download the latest release .zip from the Releases section of this repository.
2. Extract the archive folder to your preferred location.
3. Run the installer script `Install_Release.cmd` as Administrator to automatically verify systems, install Blender 4.x via winget (if absent), configure the SourceIO Blender addon, and set up required binaries.
4. Launch the application via `LOD_Generator.exe`.

Note: CrowbarCLI.exe is automatically tracked by the application. If missing, the installer handles downloading and placing it inside the designated directories.

### Developer Setup (Running from Source)

To run the application directly from the source code or make custom changes:

#### System Requirements

- Python 3.11 or higher.
- Blender 4.x or higher.
- Blender SourceIO addon (v5.5.3 recommended).
- Garry's Mod or Source SDK Base 2013 Multiplayer installed via Steam for `studiomdl.exe`.

#### Automated Developer Installation

We provide an automated PowerShell script to install Python dependencies, configure Blender via winget, install the SourceIO addon, and compile the final executable with PyInstaller:

1. Open a PowerShell terminal as Administrator at the root of this project.
2. Run the developer setup script:
   ```powershell
   Set-ExecutionPolicy Bypass -Scope Process -Force
   .\Install_Dev.ps1
   ```
3. Once completed, run the script using Python:
   ```bash
   python LOD_Generator.py
   ```

---

## Path Settings and Configuration

After launching the application, configure your working paths in the Tools panel:

- Source/GMod Game Folder: Path to your game folder containing `gameinfo.txt`. The application features an intelligent Steam library locator that automatically detects your Garry's Mod, HL2, TF2, or Portal 2 directories.
- Output Folder: Destination where generated and compiled model assets will be saved.
- studiomdl executable: Path to Valve's official model compiler (`studiomdl.exe`).
- blender executable: Path to `blender.exe`. The application scans common system paths and Steam libraries for Blender versions 3.6 to 4.3+.
- Crowbar CLI path: Location of `CrowbarCLI.exe` used to drive background decompilations.

You can save this configuration using the Save button to reload it automatically at startup.

---

## Technical Details and Generation Pipeline

### High-Level Architecture

```
LOD_Generator.py
├── VPK Extraction System         -> Handles reading and unpacking of Garry's Mod archives
├── VMF Parser                    -> Extracts prop data and occurrences from Hammer map files
├── Extraction Pipeline           -> Inspects MDL file metadata and structural files
├── QC File Parser                -> Performs syntactic modification on Source compilation QC scripts
├── Blender Integration           -> Scripting engine for procedural decimation & SMD exporting via Python
├── Crowbar CLI Integration       -> Drives background decompile and parse tasks
├── studiomdl Integration         -> Direct compilation via official Valve binaries
├── 3D Preview Engine             -> Interactive real-time renderer utilizing OpenGL and Pyglet
└── User Interface (GUI)          -> Tkinter-based responsive UI with drag-and-drop mechanics
```

### Generation Pipeline

The generation of an optimized prop moves seamlessly through the following phases:

```
[Original MDL Model]
        │
        ▼ (Decompiled via CrowbarCLI)
[QC Configuration File + SMD/DMX Geometry Meshes]
        │
        ▼ (Automated Python scripts executed in Blender)
[Reduced Geometry Files (LOD SMD Files)]
        │
        ▼ (Dynamic text manipulation of the QC File)
[Updated QC script containing newly generated LOD thresholds]
        │
        ▼ (Compiled via Valve's studiomdl)
[Final MDL Asset with embedded, ready-to-use LODs]
```

---

## Known Limitations

- Windows-only binaries: The underlying Valve SDK tools (`studiomdl.exe`) and Crowbar CLI require a native Windows environment to execute.
- Raw VMF layout: Only uncompiled, raw plaintext Hammer map files (.vmf) are supported. Compiled .bsp files cannot be read directly.
- Complex Physics meshes: Very complex ragdolls or physics constraints might require manual adjustments for collision model simplifications.

---

## AI-Assisted Development

This project was built leveraging AI-assisted development tools in a deliberate and thoughtful manner.

Using AI capabilities accelerates prototyping cycles, refines intricate algorithm design, automates validation workflows, and enhances the overall user interface experience. This acts as a major productivity multiplier while retaining complete manual verification, auditing, and deep structural understanding of the codebase. All generated systems have been thoroughly tested, validated, and integrated to ensure they match the project's high standards of quality and utility.

---

## Contributing

Contributions, bug reports, and suggestions are welcome!

1. Fork the project.
2. Create your feature branch (git checkout -b feature/AmazingFeature).
3. Commit your changes (git commit -m 'Add some AmazingFeature').
4. Push to the branch (git push origin feature/AmazingFeature).
5. Open a detailed Pull Request.

---

## Credits and Acknowledgements

- Crowbar - Decompiling and compiling companion app by ZeqMacaw.
- CrowbarCLI - Command-line interface port by UltraTechX driving our background processes.
- Blender Foundation - Creators of the excellent open-source 3D suite Blender.
- Valve Corporation - Authors of the Source Engine and model compiler studiomdl.
- Garry's Mod - For the incredibly active and passionate modding community.

---

## Contact

- Author: Lumastor
- GitHub: @Lumino-2-0 (https://github.com/Lumino-2-0)
- Discord: lumastor (ID: 554200657486413824)

---

<div align="center">

**If this project saved you time, consider leaving a star on the GitHub repository!**

</div>
