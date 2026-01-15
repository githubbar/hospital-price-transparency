# Setting up Elasticsearch on Windows

Since Docker is not available in your terminal, you need to install Elasticsearch manually to run the search index locally.

1. **Download Elasticsearch**:
   - Go to: [https://www.elastic.co/downloads/elasticsearch](https://www.elastic.co/downloads/elasticsearch)
   - Download the **Windows** ZIP archive.

2. **Install & Run**:
   - Extract the ZIP file to a folder (e.g., `C:\Elasticsearch`).
   - Open a PowerShell terminal in that folder.
   - Run: `.\bin\elasticsearch.bat`

3. **Verify**:
   - Open your browser to `http://localhost:9200`.
   - You should see a JSON response with version info.

4. **Disable Security (Optional for Development)**:
   - If you run into password/SSL issues, you can simplify the dev setup by editing `config/elasticsearch.yml`:
     ```yaml
     xpack.security.enabled: false
     ```
   - Restart Elasticsearch.

5. **Load Data**:
   - Once running, execute the loading script in VS Code:
     ```powershell
     & "X:/Hospital Price Transparency/.venv/Scripts/python.exe" load_to_es.py
     ```
