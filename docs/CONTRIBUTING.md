# Contributing Guide

## Overview
Thank you for your interest in contributing to ChinaRxiv! This guide will help you get started with contributing to the project.

## Getting Started

### Prerequisites
- Python 3.11+
- Node.js 18+
- Git
- GitHub account

### Fork and Clone
1. Fork the repository on GitHub
2. Clone your fork locally:
   ```bash
   git clone https://github.com/domus-magna/chinarxiv.git
   cd chinarxiv
   ```

### Development Setup
1. Create a virtual environment:
   ```bash
   python -m venv .venv
   source .venv/bin/activate  # On Windows: .venv\Scripts\activate
   ```

2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
   This includes dev/test tools (pytest, black, ruff, isort) already.

## Development Workflow

### Branching Strategy
- `main`: Production-ready code
- `develop`: Integration branch for features
- `feature/*`: Feature branches
- `bugfix/*`: Bug fix branches
- `hotfix/*`: Critical bug fixes

### Creating a Feature Branch
```bash
# Create and switch to feature branch
git checkout -b feature/your-feature-name

# Make your changes
# ...

# Commit your changes
git add .
git commit -m "feat: add your feature description"
```

### Commit Message Format
We use [Conventional Commits](https://www.conventionalcommits.org/):

```
<type>[optional scope]: <description>

[optional body]

[optional footer(s)]
```

#### Types
- `feat`: New feature
- `fix`: Bug fix
- `docs`: Documentation changes
- `style`: Code style changes (formatting, etc.)
- `refactor`: Code refactoring
- `test`: Adding or updating tests
- `chore`: Maintenance tasks

#### Examples
```bash
feat(translate): add batch translation support
fix(pipeline): remove Internet Archive harvesting
docs(api): update API documentation
test(monitor): add tests for monitoring service
```

## Code Quality

### Code Style
We use the following tools for code quality:

#### Python
- **Black**: Code formatting
- **Ruff**: Linting and import sorting
- **isort**: Import sorting
- **mypy**: Type checking

#### Commands
```bash
# Format code
black src tests

# Check linting
ruff check src tests

# Sort imports
isort src tests
```

### Testing

#### Running Tests
```bash
# Run all tests
python -m pytest tests/ -v

# Run specific test file
python -m pytest tests/test_translate.py -v

# Run with coverage
python -m pytest tests/ --cov=src --cov-report=html

# Run E2E tests
python -m pytest tests/test_e2e_simple.py -v
```

#### Writing Tests
- Write tests for new features
- Aim for >80% code coverage
- Use descriptive test names
- Test both success and failure cases

#### Test Structure
```python
# tests/test_feature.py
import pytest
from src.feature import FeatureClass

class TestFeatureClass:
    """Test cases for FeatureClass."""
    
    def setup_method(self):
        """Setup test environment."""
        self.feature = FeatureClass()
    
    def test_success_case(self):
        """Test successful operation."""
        result = self.feature.do_something()
        assert result is not None
        assert result.status == "success"
    
    def test_failure_case(self):
        """Test failure case."""
        with pytest.raises(ValueError):
            self.feature.do_something(invalid_input=True)
```

## Project Structure

### Directory Layout
```
chinaxiv-english/
├── src/                    # Source code
│   ├── __init__.py
│   ├── monitoring.py      # Consolidated monitoring service
│   ├── translate.py       # Translation service
│   ├── harvest_chinaxiv.py      # Direct harvest experiments (optional)
│   ├── render.py          # Site rendering
│   ├── search_index.py    # Search index generation
│   └── ...
├── tests/                 # Test files
│   ├── conftest.py
│   ├── test_translate.py
│   ├── test_monitoring.py
│   └── ...
├── docs/                  # Documentation
│   ├── SETUP.md
│   ├── DEPLOYMENT.md
│   ├── API.md
│   └── CONTRIBUTING.md
├── site/                  # Generated site
├── data/                  # Data files
├── assets/                # Static assets
├── .github/workflows/     # GitHub Actions
├── requirements.txt       # Python dependencies
└── README.md
```

### Key Components

#### Monitoring Service (`src/monitoring.py`)
Consolidated service combining alerts, analytics, and performance monitoring.

#### Translation Service (`src/translate.py`)
Handles translation of Chinese papers to English using OpenRouter API.

#### Harvesting
Internet Archive approach has been removed. Provide records under `data/records/` via custom ingestion or experimental direct harvesters.

#### Site Renderer (`src/render.py`)
Generates static HTML pages for the site.

#### Search Index (`src/search_index.py`)
Creates search index for client-side search functionality.

## Contributing Guidelines

### Pull Request Process
1. **Fork** the repository
2. **Create** a feature branch
3. **Make** your changes
4. **Add** tests for new functionality
5. **Ensure** all tests pass
6. **Update** documentation if needed
7. **Submit** a pull request

### Pull Request Template
```markdown
## Description
Brief description of changes

## Type of Change
- [ ] Bug fix
- [ ] New feature
- [ ] Breaking change
- [ ] Documentation update

## Testing
- [ ] Tests pass locally
- [ ] New tests added for new functionality
- [ ] Coverage maintained or improved

## Checklist
- [ ] Code follows project style guidelines
- [ ] Self-review completed
- [ ] Documentation updated
- [ ] No breaking changes (or documented)
```

### Review Process
1. **Automated checks** must pass
2. **Code review** by maintainers
3. **Testing** in staging environment
4. **Approval** from at least one maintainer
5. **Merge** to main branch

### Issue Reporting
When reporting issues, please include:
- **Description** of the problem
- **Steps** to reproduce
- **Expected** behavior
- **Actual** behavior
- **Environment** details
- **Screenshots** if applicable

### Feature Requests
When requesting features, please include:
- **Description** of the feature
- **Use case** and benefits
- **Proposed** implementation
- **Alternatives** considered
- **Additional** context

## Development Tips

### Local Development
```bash
# Run the full pipeline locally (harvest removed; ensure records exist if needed)
python -m src.pipeline --limit 5
python -m src.render
python -m src.search_index

# Serve the site locally
python -m http.server 8001 --directory site
```

### Debugging
```bash
# Enable debug logging
export LOG_LEVEL=DEBUG
python -m src.translate --dry-run --limit 1

# Use monitoring dashboard
python -m src.monitor
# Access at http://localhost:5000
```

### Performance Testing
```bash
# Test translation performance
python -c "
import time
from src.translate import translate_text
start = time.time()
result = translate_text('这是一个测试')
print(f'Translation took {time.time() - start:.2f} seconds')
"

# Test monitoring performance
python -c "
from src.monitoring import monitoring_service
import time
start = time.time()
for i in range(100):
    monitoring_service.create_alert('info', f'Test {i}', 'Message')
print(f'100 alerts created in {time.time() - start:.2f} seconds')
"
```

## Documentation

### Code Documentation
- Use docstrings for all functions and classes
- Follow Google docstring format
- Include type hints
- Document complex algorithms

#### Example
```python
def translate_text(text: str, model: str = "deepseek/deepseek-v3.2-exp") -> str:
    """Translate Chinese text to English.
    
    Args:
        text: Chinese text to translate
        model: Translation model to use
        
    Returns:
        Translated English text
        
    Raises:
        ValueError: If text is empty
        APIError: If translation fails
    """
    if not text:
        raise ValueError("Text cannot be empty")
    
    # Implementation here
    return translated_text
```

### README Updates
- Update README.md for significant changes
- Include new features in feature list
- Update installation instructions if needed
- Add new dependencies to requirements.txt

### API Documentation
- Update docs/API.md for API changes
- Include new endpoints
- Update data models
- Add examples

## Release Process

### Versioning
We use [Semantic Versioning](https://semver.org/):
- **MAJOR**: Breaking changes
- **MINOR**: New features (backward compatible)
- **PATCH**: Bug fixes (backward compatible)

### Release Steps
1. **Update** version in `__init__.py`
2. **Update** CHANGELOG.md
3. **Create** release branch
4. **Test** release candidate
5. **Create** GitHub release
6. **Deploy** to production

### Changelog Format
```markdown
# Changelog

## [1.1.0] - 2025-10-05

### Added
- New feature A
- New feature B

### Changed
- Improved performance of feature C
- Updated dependencies

### Fixed
- Bug fix D
- Bug fix E

### Removed
- Deprecated feature F
```

## Community Guidelines

### Code of Conduct
- Be respectful and inclusive
- Welcome newcomers
- Focus on constructive feedback
- Respect different viewpoints

### Communication
- **GitHub Issues**: Bug reports and feature requests
- **GitHub Discussions**: General questions and ideas
- **Pull Requests**: Code contributions
- **Discord**: Real-time chat and support

### Recognition
Contributors are recognized in:
- README.md contributors section
- Release notes
- GitHub contributors page
- Project documentation

## Getting Help

### Resources
- **Documentation**: Check docs/ directory
- **Examples**: Look at existing code
- **Tests**: Understand expected behavior
- **Issues**: Search for similar problems

### Support Channels
- **GitHub Issues**: Technical problems
- **GitHub Discussions**: Questions and ideas
- **Discord**: Real-time help
- **Email**: Direct contact for sensitive issues

### Mentorship
- New contributors can request mentorship
- Experienced contributors can volunteer to mentor
- Pair programming sessions available
- Code review feedback provided

## License
By contributing to this project, you agree that your contributions will be licensed under the same license as the project (MIT License).

## Thank You
Thank you for contributing to ChinaXiv Translations! Your contributions help make academic research more accessible to the global community.
