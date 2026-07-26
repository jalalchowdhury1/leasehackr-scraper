import os
import sys

# Make the repo root importable so tests can `import scraper` / `import fetcher`.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
