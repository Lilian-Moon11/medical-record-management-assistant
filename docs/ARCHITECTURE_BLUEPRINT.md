# Architectural Blueprint: Medical Record Management Assistant (MRMA)

## 1. Introduction: The Convergence of Privacy, Accessibility, and Local Computing
The digital transformation of healthcare workflows has traditionally relied on cloud-centric architectures to manage the complexity of electronic health records (EHRs). However, a growing paradigm shift towards "Local-First" software is emerging, driven by stringent data privacy regulations (HIPAA, GDPR), the need for offline resilience, and the desire for absolute data sovereignty. 

MRMA was engineered at the vanguard of this shift to deliver the capabilities of a cloud-based RAG (Retrieval-Augmented Generation) system within the constraints of consumer hardware, while simultaneously ensuring the interface is fully accessible to users with disabilities (WCAG 2.1) and the data remains securely encrypted.

### 1.1 The Imperative of Local Data Sovereignty
Storing medical data solely on a user's local machine eliminates transmission vectors vulnerable to cyberattacks (Man-in-the-Middle, cloud database breaches). However, this transfers the entire burden of security—encryption at rest, access control, and integrity validation—onto the application itself. The application must function as a self-contained fortress while resolving the tension between data integrity (ACID compliance) and user agency.

### 1.2 The Accessibility Compliance Landscape
Accessibility in medical software is a legal and ethical imperative. Digital healthcare tools must be usable by individuals utilizing assistive technologies (AT) like screen readers (NVDA, JAWS) and screen magnifiers. The architectural choice of a native-rendered UI over a web-wrapper is the single most deterministic factor in achieving a compliant, equitable user experience on older hardware.

---

## 2. The User Interface Architecture: Evaluating Accessibility
The choice of Graphical User Interface (GUI) dictates the application's accessibility foundation. 

### 2.1 The Architectural Limitations of Immediate-Mode Web Wrappers (Streamlit)
While frameworks like Streamlit offer rapid data-science prototyping, their "immediate mode" execution model (re-rendering the DOM on every interaction) creates a hostile environment for assistive technologies. Full re-renders reset screen reader focus, violating the WCAG "Focus Management" criterion and rendering complex medical forms practically unusable for blind users.

### 2.2 The Superior Alternative: Flet (Flutter for Python)
Flet represents a paradigm shift for desktop-first Python applications. It renders its own widget tree on a Skia canvas and maintains a parallel **Semantics Tree** specifically for accessibility services.
- **Native Accessibility APIs**: Flet interfaces directly with the OS (UIAutomation on Windows, NSAccessibility on macOS), allowing the app to behave natively.
- **Traversal Order Control**: Flet enables explicit definition of `traversal_index`, ensuring keyboard navigation moves in a logical sequence defined by the developer.
- **Performance**: Compiled Flet apps exhibit a smaller memory footprint and smoother animation compared to heavy Chromium instances, which is critical for the "older computer" target demographic.

---

## 3. Data Architecture: Secure Persistence
Standard user-editable formats (CSV) lack the relational integrity and encryption needed for medical records.

### 3.1 SQLCipher Integration
To satisfy HIPAA-grade security requirements, the application utilizes SQLCipher (transparent 256-bit AES encryption). Upon startup, the user's password is run through a Key Derivation Function (PBKDF2) to generate the encryption key, ensuring the file on disk remains an encrypted binary blob.

### 3.2 Secure File Storage Strategy (Fernet)
Medical records often include PDF scans. Storing these as loose files is a severe security risk. The application uses Fernet symmetric encryption to encrypt raw PDF files upon ingestion. When the user (or AI) needs to read the file, the app decrypts the file directly into memory (RAM) and processes it without ever writing the unencrypted payload back to the disk.

---

## 4. Intelligent Document Processing & RAG Pipeline
The core innovation is the ability to extract information from medical records and create summaries with verifiable citations, entirely offline.

### 4.1 The Local Inference Engine: SLMs over LLMs
Running massive LLMs locally is impossible. The application leverages high-performance "Small Language Models" (SLMs) like **Phi-3-Mini (3.8B)** or **Qwen 2.5**, which fit within consumer RAM constraints (8GB).
- **Optimization via Quantization**: The models are deployed in GGUF format (4-bit quantization). This reduces the memory footprint by ~70% with negligible loss in accuracy.
- **Engine**: I explicitly chose `llama-cpp-python` over managing an external background service like Ollama. It bypasses bloated machine-learning setups (like massive PyTorch installations), keeping the final executable package incredibly lean.

### 4.2 The Citation Architecture
To prevent hallucinations and provide "Attributed Q&A", the standard RAG process was modified using LlamaIndex's `CitationQueryEngine`. During ingestion, documents are chunked and injected with metadata (Page Numbers, Source Files). The engine instructs the LLM to cite these source numbers, generating verifiable medical summaries.

---

## 5. The Intake Workflow: Accessible Form Filling
Filling a PDF form programmatically often breaks its accessibility features (tags), rendering the final document unreadable to screen readers.

### 5.1 PyPDFForm for Tag Preservation
`PyPDFForm` is utilized to inject data into AcroForm widgets. Because it does not reconstruct the entire PDF structure or "flatten" the document into an image, the existing accessibility tags (structure tree) remain intact, ensuring the filled PDF remains compliant for subsequent digital distribution.

---

## 6. Packaging and Deployment Strategy
Delivering this application to users with low technical literacy requires a seamless installation experience without bloated dependencies.

### 6.1 PyInstaller Performance Bottlenecks
PyInstaller's `--onefile` mode has notoriously slow startup times on HDDs because it unpacks a temporary filesystem every launch. 
- **Strategy**: The build pipeline uses `--onedir` mode. The resulting directory is wrapped into a standard installer (like Inno Setup), which places the pre-unpacked files in Program Files. This ensures instantaneous startup times, crucial for the perception of performance on legacy hardware.

### 6.2 Bypassing Developer Signatures
As an independent, free open-source project, no code-signing certificates were purchased. Clear, plain-language documentation is provided to users on how to safely bypass Windows SmartScreen and macOS Gatekeeper warnings upon initial execution.
