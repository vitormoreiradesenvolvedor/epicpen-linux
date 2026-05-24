import sys
from pathlib import Path

# Adiciona src/ ao path para que os módulos sejam encontrados pelo pytest
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
