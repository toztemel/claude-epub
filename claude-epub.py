name: Build EPUBs
on:
  workflow_dispatch:
  push:
    branches: [main]
permissions:
  contents: write
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Install tools
        run: |
          sudo apt-get update -q
          sudo apt-get install -y -q pandoc
          pip install requests beautifulsoup4 readability-lxml lxml_html_clean
      - name: Build books
        run: python3 claude_epub.py
      - name: Publish release
        env:
          GH_TOKEN: ${{ github.token }}
        run: |
          TAG="books-$(date +%Y-%m-%d)-${{ github.run_number }}"
          gh release create "$TAG" *.epub --title "Claude books" --notes "Offline EPUBs"