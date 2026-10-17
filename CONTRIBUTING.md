# Contributing to Scripta

Thank you for your interest in contributing to **Scripta**! We welcome contributions from researchers, digital humanists, archivists, and developers worldwide.

---

## 🛠️ Development Setup

1. **Fork and Clone**:
   ```bash
   git clone https://github.com/<your-username>/Scripta.git
   cd Scripta
   ```

2. **Set up Python Virtual Environment**:
   ```bash
   python -m venv venv
   # On Windows:
   .\venv\Scripts\activate
   # On Linux / macOS:
   source venv/bin/activate
   ```

3. **Install Dependencies & Test Tools**:
   ```bash
   pip install -r requirements.txt
   pip install pytest pytest-cov ruff
   ```

4. **Verify Environment**:
   ```bash
   pytest tests -v
   ```

---

## 🌿 Branching Strategy & Workflow

1. Create a descriptive feature branch:
   ```bash
   git checkout -b feat/your-feature-name
   # or for bug fixes:
   git checkout -b fix/issue-description
   ```
2. Write clean, modular Python code adhering to PEP 8 standards.
3. Write corresponding unit tests in `tests/` covering your changes.
4. Run tests and verify syntax before committing:
   ```bash
   python -m py_compile book_digitizer/*.py
   pytest tests
   ```

---

## 📝 Commit Conventions

We follow the [Conventional Commits](https://www.conventionalcommits.org/) specification:
- `feat:` New functional feature or capability
- `fix:` Bug fix or error resolution
- `docs:` Documentation improvements or updates
- `test:` Adding or updating test cases
- `refactor:` Code restructuring without behavioral changes
- `perf:` Performance improvements
- `ci:` Continuous integration configuration changes

---

## 📬 Submitting a Pull Request

1. Push your branch to your GitHub fork:
   ```bash
   git push origin feat/your-feature-name
   ```
2. Open a Pull Request against the `main` branch of the upstream repository.
3. Provide a clear description of the problem solved, any architectural decisions, and verification steps.
