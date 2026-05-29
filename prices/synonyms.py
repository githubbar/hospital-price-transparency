import re
import json
from django.db import connection

# In-memory cache for loaded synonyms patterns
_cached_synonyms = None

def clear_synonyms_cache():
    """Clears the loaded in-memory synonyms cache."""
    global _cached_synonyms
    _cached_synonyms = None

def load_synonyms_from_db():
    """
    Loads synonyms from the packaged default_synonyms.json first,
    merges them with database-defined synonyms if available,
    and returnscompiled patterns sorted by phrase length descending.
    This bypasses the need to rebuild/upload the 4.8 GB SQLite file to update synonyms.
    """
    global _cached_synonyms
    if _cached_synonyms is not None:
        return _cached_synonyms

    synonyms_dict = {}

    # 1. Load from default_synonyms.json (packaged with the container)
    try:
        from django.conf import settings
        import os
        json_path = os.path.join(settings.BASE_DIR, 'reference', 'default_synonyms.json')
        if os.path.exists(json_path):
            with open(json_path, 'r', encoding='utf-8') as f:
                default_synonyms = json.load(f)
            for phrase, expansions in default_synonyms.items():
                if isinstance(expansions, list):
                    synonyms_dict[phrase.strip().lower()] = expansions
            print(f"Loaded {len(synonyms_dict)} synonyms from reference JSON.")
        else:
            print(f"Fallback synonyms JSON not found at: {json_path}")
    except Exception as json_err:
        print(f"Error loading fallback synonyms JSON: {json_err}")

    # 2. Merge with database synonyms if available
    try:
        with connection.cursor() as cursor:
            # Check if synonyms table exists first
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='synonyms';")
            if cursor.fetchone():
                cursor.execute("SELECT phrase, expansions FROM synonyms")
                rows = cursor.fetchall()
                for phrase, exp_json in rows:
                    if not phrase or not exp_json:
                        continue
                    try:
                        expansions = json.loads(exp_json)
                        if isinstance(expansions, list):
                            synonyms_dict[phrase.strip().lower()] = expansions
                    except Exception as json_err:
                        print(f"Error parsing database synonym expansions for '{phrase}': {json_err}")
                print(f"Successfully merged synonyms from database.")
    except Exception as db_err:
        print(f"Database error loading synonyms: {db_err}")

    # 3. Sort by phrase length descending and compile regex patterns
    sorted_syns = sorted(synonyms_dict.items(), key=lambda item: len(item[0]), reverse=True)
    synonyms_list = []
    for phrase, expansions in sorted_syns:
        pattern = re.compile(rf'\b{re.escape(phrase)}\b', re.IGNORECASE)
        synonyms_list.append((pattern, expansions))

    _cached_synonyms = synonyms_list
    return _cached_synonyms

def expand_query_synonyms(query):
    """
    Scans the query for database-defined synonym matches.
    Replaces matched phrases with placeholders (e.g. __SYN_0__) to protect them from spellcheck.
    Returns:
        (processed_query, placeholder_map)
    """
    if not query:
        return query, {}
        
    processed_query = query
    placeholder_map = {}
    placeholder_counter = 0
    
    # Load dynamically from DB
    syn_patterns = load_synonyms_from_db()
    
    for pattern, expansions in syn_patterns:
        # Use regex search to find matches
        match = pattern.search(processed_query)
        if match:
            matched_text = match.group(0)
            placeholder = f"__SYN_{placeholder_counter}__"
            
            # Format FTS5 OR list, e.g. (95115 OR 95117 OR "immunotherapy injection" OR ("allergy" AND "shot"))
            # Make sure we also include the user's literal matched text (properly escaped) so it remains part of the potential match
            escaped_literal = f'"{matched_text}"'
            # Convert matched text to word AND chain as fallback
            words = [w for w in re.findall(r'\w+', matched_text) if w]
            fallback_chain = f"({' AND '.join(words)})" if len(words) > 1 else words[0]
            
            all_options = list(expansions)
            if escaped_literal not in all_options:
                all_options.append(escaped_literal)
            if fallback_chain not in all_options:
                all_options.append(fallback_chain)
                
            fts5_clause = f"({' OR '.join(all_options)})"
            
            placeholder_map[placeholder] = fts5_clause
            # Replace all occurrences of the pattern with the placeholder
            processed_query = pattern.sub(placeholder, processed_query)
            placeholder_counter += 1
            
    return processed_query, placeholder_map

def inject_synonyms_into_fts(sqlite_query, placeholder_map):
    """
    Replaces temporary synonym placeholders back with their FTS5 sub-queries.
    """
    if not sqlite_query or not placeholder_map:
        return sqlite_query
        
    result_query = sqlite_query
    for placeholder, fts5_clause in placeholder_map.items():
        # Match placeholder (with optional wildcard appended, e.g. __SYN_0__*)
        # Since synonyms are already expanded to exact phrases and codes, we don't want the FTS builder's star:
        result_query = result_query.replace(placeholder + "*", fts5_clause)
        result_query = result_query.replace(placeholder, fts5_clause)
        
    return result_query
