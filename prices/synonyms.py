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
    Fetches synonyms from the database, sorted by length in descending order
    (so longer phrases are matched before shorter sub-phrases).
    Compiles word-boundary patterns dynamically and caches the result.
    """
    global _cached_synonyms
    if _cached_synonyms is not None:
        return _cached_synonyms

    synonyms_list = []
    try:
        with connection.cursor() as cursor:
            # Check if synonyms table exists first to avoid crashes in early stages of migration
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='synonyms';")
            if not cursor.fetchone():
                return []
                
            cursor.execute("SELECT phrase, expansions FROM synonyms ORDER BY length(phrase) DESC")
            rows = cursor.fetchall()
            
            for phrase, exp_json in rows:
                if not phrase or not exp_json:
                    continue
                try:
                    expansions = json.loads(exp_json)
                    if isinstance(expansions, list):
                        # Compile word boundaries dynamically
                        pattern = re.compile(rf'\b{re.escape(phrase.strip())}\b', re.IGNORECASE)
                        synonyms_list.append((pattern, expansions))
                except Exception as json_err:
                    print(f"Error parsing synonym expansions for '{phrase}': {json_err}")
    except Exception as db_err:
        print(f"Database error loading synonyms: {db_err}")
        return []

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
