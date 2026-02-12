from env import *
import sqlite3

EVENTS_DB_FILENAME = env['events']['eventsDbFilename']
DB_FILENAME = env['dbFilename']

def dbGetStats():
    result = []
    
    try:
        wps_select_query = f"""
        SELECT  
            1 as "Sort",
            "Posts" as "Category",
            "Total Posts" as "Statistic",
            COUNT(json_extract(post, '$.ts')) as count
        FROM posts
        UNION
        SELECT  
            2 as "Sort",
            "Messages" as "Category",
            "Total Messages" as "Statistic",
            COUNT(json_extract(message, '$.ts')) as count
        FROM messages
        UNION
        SELECT  
            3 as "Sort",
            "Posts" as "Category",
            "Total Posts Today" as "Statistic",
            COUNT(json_extract(post, '$.ts')) as count
        FROM posts
            WHERE DATE(ROUND(json_extract(post, '$.ts')/1000), 'unixepoch') BETWEEN datetime('now', '-2 days') AND datetime('now', '-1 days')
        UNION
        SELECT  
            4 as "Sort",
            "Messages" as "Category",
            "Total Messages Today" as "Statistic",
            COUNT(json_extract(message, '$.ts')) as count
        FROM messages
            WHERE DATE(ROUND(json_extract(message, '$.ts')), 'unixepoch') BETWEEN datetime('now', '-2 days') AND datetime('now', '-1 days')
        UNION
        SELECT  
            5 as "Sort",
            "Posts" as "Category",
            "Total Posts Last 7 Days" as "Statistic",
            COUNT(json_extract(post, '$.ts')) as count
        FROM posts
        WHERE
            DATE(ROUND(json_extract(post, '$.ts') / 1000), 'unixepoch') BETWEEN datetime('now', '-8 days') AND datetime('now', '-1 days')
        UNION
        SELECT  
            6 as "Sort",
            "Messages" as "Category",
            "Total Messages Last 7 Days" as "Statistic",
            COUNT(json_extract(message, '$.ts')) as count
        FROM messages
        WHERE
            DATE(ROUND(json_extract(message, '$.ts')), 'unixepoch') BETWEEN datetime('now', '-8 days') AND datetime('now', '-1 days')
        UNION
        SELECT 
            7 as "Sort",
            "General" as "Category",
            "Unique Connecting Users Last 7 Days" as "Statistic",
            COUNT(json_extract(user, '$.callsign')) as count
        FROM 
            users
        WHERE
        DATE(ROUND(json_extract(user, '$.last_connected')), 'unixepoch') BETWEEN datetime('now', '-8 days') AND datetime('now', '-1 days')
        UNION
        SELECT 
            8 as "Sort",
            "Posts" as "Category",
            "Top 5 Posters Last 7 Days" as "Statistic",
            GROUP_CONCAT(callsign || ': ' || count, ', ' ) as statistic
        FROM (SELECT  
                json_extract(post, '$.fc') as callsign,
                COUNT(json_extract(post, '$.ts')) as count
            FROM posts
            WHERE
                DATE(ROUND(json_extract(post, '$.ts') / 1000), 'unixepoch') BETWEEN datetime('now', '-8 days') AND datetime('now', '-1 days')
            GROUP BY callsign
            ORDER BY count DESC
            LIMIT  5)
        UNION
        SELECT
            9 as "Sort",
            "Messages" as "Category",
            "Most Messages in a day" as "Statistic",
            GROUP_CONCAT(date || ': ' || messagecount, ', ' ) as statistic
        FROM
            (SELECT  
                DATE(ROUND(json_extract(message, '$.ts')), 'unixepoch')  as date,
                COUNT(json_extract(message, '$.ts')) as messagecount
            FROM messages
            GROUP BY date
            ORDER BY messagecount DESC
            LIMIT 1)
        UNION
        SELECT
            10 as "Sort",
            "Posts" as "Category",
            "Most Posts in a day" as "Statistic",
            GROUP_CONCAT(date || ': ' || postcount, ', ' ) as statistic
        FROM
            (SELECT  
                DATE(ROUND(json_extract(post, '$.ts')/1000), 'unixepoch')  as date,
                COUNT(json_extract(post, '$.ts')) as postcount
            FROM posts
            GROUP BY date
            ORDER BY postcount DESC
            LIMIT 1)
        UNION
        SELECT
            11 as "Sort",
            "Messages" as "Category",
            "Most active messager in a day" as "Statistic",
            GROUP_CONCAT(callsign || ': ' || messagecount, ', ' ) as statistic
        FROM
            (SELECT  
                DATE(ROUND(json_extract(message, '$.ts')), 'unixepoch')  as date,
                COUNT(json_extract(message, '$.ts')) as messagecount,
                json_extract(message, '$.fc') as callsign
            FROM messages
            GROUP BY date, callsign
            ORDER BY messagecount DESC
            LIMIT 1)
        UNION
        SELECT
            12 as "Sort",
            "Posts" as "Category",
            "Most active poster in a day" as "Statistic",
            GROUP_CONCAT(callsign || ': ' || postcount, ', ' ) as statistic
        FROM
            (SELECT  
                DATE(ROUND(json_extract(post, '$.ts')/1000), 'unixepoch')  as date,
                COUNT(json_extract(post, '$.ts')) as postcount,
                json_extract(post, '$.fc') as callsign
            FROM posts
            WHERE
                json_extract(post, '$.cid') != 6
            GROUP BY date, callsign
            ORDER BY postcount DESC
            LIMIT 1)
        ORDER BY 
            Category DESC, Sort ASC
        """

        events_select_query = f"""
        SELECT 
            1 as "Sort",
            "General" as "Category",
            "Bytes sent previous day" as "Statistic",
            sum(json_extract(event, '$.e.bytes')) AS bytes_count
        FROM 
            events
        WHERE
            json_extract(event, '$.et') = "WPS_SEND" AND
            DATE(ROUND(json_extract(event, '$.ts')/1000), 'unixepoch') BETWEEN datetime('now', '-2 days') AND datetime('now', '-1 days') 
        UNION
        SELECT 
            2 as "Sort",
            "General" as "Category",
            "Bytes sent previous 30 days" as "Statistic",
            sum(json_extract(event, '$.e.bytes')) AS bytes_count
        FROM 
            events
        WHERE
            json_extract(event, '$.et') = "WPS_SEND" AND
            DATE(ROUND(json_extract(event, '$.ts')/1000), 'unixepoch') BETWEEN datetime('now', '-31 days') AND datetime('now', '-1 days') 
        UNION
        SELECT  
            3 as "Sort",
            "General" as "Category",
            "WPS responses sent previous day" as "Statistic",
            count(json_extract(event, '$.ts')) AS send_count
        FROM events
        WHERE
            json_extract(event, '$.et') = "WPS_SEND" AND
            DATE(ROUND(json_extract(event, '$.ts')/1000), 'unixepoch') BETWEEN datetime('now', '-2 days') AND datetime('now', '-1 days') 
        UNION
        SELECT  
            4 as "Sort",
            "General" as "Category",
            "WPS responses sent previous 30 days" as "Statistic",
            count(json_extract(event, '$.ts')) AS send_count
        FROM events
        WHERE
            json_extract(event, '$.et') = "WPS_SEND" AND
            DATE(ROUND(json_extract(event, '$.ts')/1000), 'unixepoch') BETWEEN datetime('now', '-31 days') AND datetime('now', '-1 days') 
        ORDER BY
            Sort ASC
        """

        merged_database_results = []

        with sqlite3.connect(DB_FILENAME) as conn:
            cursor = conn.cursor()

            cursor.execute(wps_select_query)
            conn.commit()

            merged_database_results = list(cursor)

        with sqlite3.connect(EVENTS_DB_FILENAME) as conn:
            cursor = conn.cursor()

            cursor.execute(events_select_query)
            conn.commit()

            merged_database_results = merged_database_results + list(cursor)

        for row in merged_database_results:                
            result.append({ "cat": row[1], "stat": row[2], "val": row[3] })
        
        return_success = {
            "result": "success",
            "data": result
        }
        
        return return_success

    except Exception as e:
        return_error = {
            "result": "failure",
            "error": str(e),
            "function": "dbGetEventStats",
            "params": ''
        }
        
        return return_error

print(json.dumps(dbGetStats(), indent=4))