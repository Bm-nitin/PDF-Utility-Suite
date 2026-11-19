# PDF Utility Suite

**A local-first PDF toolkit for Windows, built with Python.**

PDF Utility Suite brings everyday PDF operations into one desktop application. It is designed to help users manage documents locally without depending on online PDF-processing services.

The project focuses on practical functionality, privacy, responsive processing, document integrity, and automated testing.

> **Development status:** Active development. Some improvements, compression safeguards, regression testing, and Windows release-packaging work are still in progress.
>
> **License:** Pending. No open-source license has been selected yet.

## Table of Contents

- [Overview](#overview)
- [Features](#features)
- [Offline-First Design](#offline-first-design)
- [Technology Stack](#technology-stack)
- [Architecture](#architecture)
- [Reliability and Security](#reliability-and-security)
- [Compression and Document Integrity](#compression-and-document-integrity)
- [Installation](#installation)
- [Running the Application](#running-the-application)
- [Running Tests](#running-tests)
- [Building for Windows](#building-for-windows)
- [Project Structure](#project-structure)
- [Current Development Status](#current-development-status)
- [Limitations](#limitations)
- [License](#license)

## Overview

Working with PDF documents often means switching between multiple tools for merging files, extracting pages, rotating documents, compressing files, or converting images.

PDF Utility Suite brings these operations together in a single desktop interface.

The application is being developed around three principles:

1. **Local processing:** Keep document operations on the user's computer.
2. **Correctness over superficial optimization:** An output file is useful only if its content remains intact.
3. **Practical usability:** Make common document operations accessible through a straightforward desktop interface.

The Windows app uses WPF for the interface and Python with PyMuPDF for document processing.

## Features

The application includes 14 PDF operations.

| Feature | Description |
|---|---|
| Merge PDF | Combine multiple PDF documents into one file. |
| Compress PDF | Optimize PDF files to reduce file size where possible. |
| Merge and Compress | Merge multiple PDFs and optimize the resulting document. |
| Split PDF | Divide a PDF into separate documents. |
| Remove Pages | Remove selected pages from a document. |
| Extract Pages | Save selected pages into a separate PDF. |
| Organize Pages | Rearrange pages into a different order. |
| Rotate Pages | Rotate PDF pages to the required orientation. |
| Protect PDF | Apply password protection to supported documents. |
| Unlock PDF | Remove supported password restrictions when the user is authorized to do so. |
| Page Numbers | Add page numbering to PDF documents. |
| Watermark PDF | Apply text watermarks to documents. |
| Images to PDF | Convert image files into a PDF document. |
| PDF to Images | Export PDF pages as image files. |

The exact behavior and available options may vary by operation. Improvements to the interface, processing, and validation are ongoing.

## Offline-First Design

PDF Utility Suite is designed for local document processing.

- PDF operations run on the user's computer.
- Core document processing does not require uploading files to a remote PDF service.
- The application is intended to work without an internet connection during normal local processing.
- Documents remain under the user's local control.
- The application avoids unnecessary reliance on cloud-based document conversion.

**Privacy note:** Local processing reduces exposure to third-party services, but it does not replace operating-system security, safe file handling, or appropriate protection of stored documents.

Development, dependency installation, updates, and downloading the application may require an internet connection.

## Technology Stack

| Technology | Purpose |
|---|---|
| Python | Application logic and PDF-processing workflows |
| WPF | Windows desktop interface, hosted by Windows PowerShell |
| PyMuPDF | PDF reading, editing, rendering, and optimization |
| Pillow | Image handling and image-processing support |
| pytest | Automated testing and regression detection |
| PyInstaller | Planned Windows executable packaging |

The project is developed and tested primarily in a Windows environment.

## Architecture

The application separates interface responsibilities from document processing.

### Desktop interface

The WPF interface provides all 12 tool workspaces. Merge & Compress, Organize Pages, and Images → PDF show the new order as you drag. The Settings page is inside the main window and includes Light and Dark themes, the installed version, and Check for Updates.

### PDF processing engine

`pdf_engine.py` contains the core PDF-processing operations, including merging, compression, page manipulation, protection, and conversion workflows.

### File management

`file_manager.py` handles file-import and file-management responsibilities.

### Configuration

`config.py` contains application configuration, including relevant processing settings and compression preset information.

### Data models

`models.py` contains shared data structures used by the application.

### Application entry point

`app.py` starts the desktop application.

### Background processing

The WPF window exchanges commands, progress, and results with the Python process over local pipes. Document processing and thumbnail rendering run in sequence outside the UI thread.

## Reliability and Security

Producing a PDF that opens successfully is not enough. Its pages, images, text, and other content must remain usable.

The project incorporates or is developing the following safeguards:

- Input validation before processing.
- PDF-specific file selection and duplicate-file handling.
- Background execution for long-running operations.
- Main-thread coordination for interface updates.
- Atomic output-saving mechanisms where implemented.
- Collision-safe output naming.
- Preservation of original source files during processing.
- Automated tests for processing behavior and regression prevention.
- Additional image-integrity testing for compression workflows.

These measures are intended to make failures easier to detect and reduce the risk of damaging source documents.

## Compression and Document Integrity

Compression is one of the most important areas of ongoing development.

A smaller file is not a successful result if its images become black, corrupted, or unreadable.

During testing, an image-recompression approach was found to damage certain image-heavy PDFs. The unsafe lossy image-recompression path has therefore been disabled while the safer approach is investigated.

The current compression strategy retains structural PDF optimization, including applicable garbage collection and stream compression options, without relying on that unsafe lossy image-recompression path.

### Current compression limitations

- The amount of size reduction depends on the PDF's existing structure and contents.
- Already-compressed images may not become significantly smaller through structural optimization.
- The Recommended and Maximum presets may provide limited additional reduction for image-heavy PDFs while lossy image recompression remains disabled.
- Exact output sizes cannot be guaranteed.
- Compression safety and regression coverage are being improved.

The goal is to introduce further size reduction only when the implementation can preserve document integrity.

## Installation

### Prerequisites

- Windows with Windows PowerShell 5.1
- Python 3.10 or a compatible version supported by the project dependencies
- Git
- A terminal such as Windows PowerShell

### 1. Clone the repository

git clone https://github.com/Bm-nitin/PDF-Utility-Suite.git
cd PDF-Utility-Suite


### 2. Create a virtual environment


python -m venv .venv


### 3. Activate the environment

.\.venv\Scripts\Activate.ps1

If PowerShell prevents script activation, use the virtual environment's Python executable directly:

.\.venv\Scripts\python.exe --version


### 4. Install dependencies

If `requirements.txt` is present in the repository root:


python -m pip install -r requirements.txt


If the project dependency list changes, use the current requirements file from the repository rather than relying on an outdated package list.

## Running the Application

From the project root, run:


python app.py


If the virtual environment is not activated, use:


.\.venv\Scripts\python.exe app.py


The desktop interface should open if the dependencies are installed correctly and the local environment is configured.

## Running Tests

The project uses pytest to validate PDF-processing behavior and catch regressions.

Run the complete test suite:


python -m pytest tests/ -v


Run tests while treating warnings as errors:


python -m pytest tests/ -v -W error


For compression-specific verification:


python -m pytest tests/test_compression_image_safety.py tests/test_pdf_engine.py tests/test_ui_compress_and_merge_compress.py -v -W error


The compression tests cover image-heavy documents, source-file preservation, rendering integrity, and protection against reintroducing the unsafe image-recompression behavior.

Test results depend on the current code revision and environment. Run the tests locally before treating a particular result as the current release status.

## Building for Windows

A standalone Windows executable is a planned distribution format.

The project uses PyInstaller for packaging. The final executable build still requires verification against the current source, dependencies, assets, and target Windows environment.

The release process should verify:

- The packaged application launches successfully.
- All 14 operations are available and function correctly.
- PDF files are processed locally.
- Image-heavy PDFs remain readable after compression.
- Source files are preserved.
- Output files are saved correctly.
- The application handles errors without crashing.
- The packaged application works on a clean Windows environment.

Until these checks are complete, the standalone executable should not be considered a verified release.

## Project Structure

The principal source files and directories are organized as follows:


PDF-Utility-Suite/
├── app.py
├── ui.py
├── pdf_engine.py
├── file_manager.py
├── models.py
├── config.py
├── requirements.txt
├── tests/
├── .github/
│   └── workflows/
├── assets/
└── README.md


Some optional directories, workflow files, or assets may differ between development revisions. Refer to the actual repository contents for the current structure.

## Current Development Status

**Status: Work in progress**

The project has progressed beyond a basic PDF merger into a multi-operation desktop utility.

### Implemented functionality

- PDF merging and compression workflows.
- Page splitting, extraction, removal, rotation, and organization.
- Password protection and supported unlocking operations.
- Page numbering and watermarking.
- Image-to-PDF and PDF-to-image conversion.
- Background processing and interface progress coordination.
- Automated tests for core processing behavior.
- Visual page previews and drag-and-drop reordering in supported workflows.

### Work underway

- Safer lossy image recompression for meaningful size reduction.
- Additional compression regression and rendering verification.
- Resolution of remaining deferred UI scrollbar test failures.
- Windows packaging and standalone executable verification.
- Final integration and release testing.

The development status will be updated as these items are completed and verified.

## Limitations

- Compression results depend on the input document.
- The current safety-first compression approach may not achieve aggressive reductions for image-heavy PDFs.
- Password-protected PDFs may require the correct password and supported document permissions.
- Some PDF features or unusual documents may require additional validation.
- The standalone Windows executable has not been confirmed as a completed release.
- This project is still under active development and may contain defects.

Always retain an appropriate backup of important documents. The application is designed to preserve source files, but backups remain sensible because computers occasionally find new and exciting ways to ruin someone's afternoon.

## License

**License pending.**

A license has not yet been selected for this project. Until one is added, users should not assume that the repository grants permission to redistribute, modify, or use the software under an open-source license.

A license file will be added after the licensing decision is made.

## Project Goals

PDF Utility Suite aims to provide a practical, reliable, and privacy-conscious desktop alternative for everyday PDF operations.

The guiding principle is simple: **correct results matter more than impressive-looking compression numbers.**

The project remains under active development, with reliability, local processing, and verified behavior taking priority over prematurely declaring the work finished.
