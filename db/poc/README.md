# Rule:

1. no using mutable function in all non-list sqls (.sql not end with _list)
2. no calling http_get_content in _list table
3. If mutable function or certain mutable convertion is used, extract
    the logic and wrap the logic into a immutable function in 
    db/immutable_func.sql
4. all poc sql correspond to a table/views. 
5. The purpose of _list sql is to allow the entity in the data model 
    to be recorded in a slowly populated table. 
    To avoid fast http crawling for the downstream table/views
6. every table should have unique rows