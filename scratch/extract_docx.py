import zipfile
import xml.etree.ElementTree as ET
import os

def read_docx(file_path):
    if not os.path.exists(file_path):
        return f"File {file_path} does not exist"
    try:
        with zipfile.ZipFile(file_path) as docx:
            xml_content = docx.read('word/document.xml')
            tree = ET.fromstring(xml_content)
            
            # Namespace map
            ns = {
                'w': 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
            }
            
            # Find all paragraph elements
            paragraphs = tree.findall('.//w:p', ns)
            text_runs = []
            for p in paragraphs:
                texts = p.findall('.//w:t', ns)
                if texts:
                    p_text = "".join([t.text for t in texts if t.text])
                    text_runs.append(p_text)
            return "\n".join(text_runs)
    except Exception as e:
        return f"Error reading {file_path}: {str(e)}"

# Read the two docx files
workspace_dir = r"x:\Hospital Price Transparency\internal docs"
for filename in ["forprofit startup pitch.docx", "nonprofit pitch.docx"]:
    path = os.path.join(workspace_dir, filename)
    print(f"=== Content of {filename} ===")
    content = read_docx(path)
    print(content[:5000])  # print first 5000 chars
    print("\n" + "="*40 + "\n")
