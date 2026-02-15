from env import *
import sqlite3

EVENTS_DB_FILENAME = env['events']['eventsDbFilename']
DB_FILENAME = env['dbFilename']

def dbGetStats():
    result = {
        "h": {},
        "p": [],
        "m": [],
        "s": []
    }
        
    unique_connecting_users_query = """
    SELECT 
        COUNT(json_extract(user, '$.callsign')) as count
    FROM 
        users
    WHERE
        CAST(json_extract(user, '$.last_connected') AS INTEGER) >= strftime('%s','now','localtime','start of day','-7 days') AND
        CAST(json_extract(user, '$.last_connected') AS INTEGER) <  strftime('%s','now','localtime','start of day')
    """
    
    posts_select_query = f"""
    SELECT  
        1 as "Sort",
        "Posts" as "Category",
        "Total Posts" as "Statistic",
        COUNT(json_extract(post, '$.ts')) as count
    FROM posts
    UNION
    SELECT  
        3 as "Sort",
        "Posts" as "Category",
        "Posts Today So Far" as "Statistic",
        COUNT(json_extract(post, '$.ts')) as count
    FROM posts
    WHERE 
        CAST(json_extract(post, '$.ts') AS INTEGER) >= strftime('%s','now','localtime','start of day') * 1000
    UNION
    SELECT  
        5 as "Sort",
        "Posts" as "Category",
        "Total Posts Last 7 Days" as "Statistic",
        COUNT(json_extract(post, '$.ts')) as count
    FROM posts
    WHERE
        CAST(json_extract(post, '$.ts') AS INTEGER) >= strftime('%s','now','-7 days') * 1000
        AND CAST(json_extract(post, '$.ts') AS INTEGER) <= strftime('%s','now') * 1000
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
            CAST(json_extract(post, '$.ts') AS INTEGER) >= strftime('%s','now','-7 days') * 1000 AND
            CAST(json_extract(post, '$.ts') AS INTEGER) <= strftime('%s','now') * 1000
        GROUP BY 
            callsign
        ORDER BY 
            count DESC
        LIMIT  5)
    UNION
    SELECT  
        9 as "Sort",
        "Posts" as "Category",
        "Total Posts Last 30 Days" as "Statistic",
        COUNT(json_extract(post, '$.ts')) as count
    FROM posts
    WHERE
        CAST(json_extract(post, '$.ts') AS INTEGER) >= strftime('%s','now','-30 days') * 1000 AND
        CAST(json_extract(post, '$.ts') AS INTEGER) <= strftime('%s','now') * 1000
    UNION
    SELECT
        10 as "Sort",
        "Posts" as "Category",
        "Most Posts in 1 Day" as "Statistic",
        GROUP_CONCAT(date || ': ' || postcount, ', ' ) as statistic
    FROM
        (SELECT  
            strftime('%d-%m-%Y', ROUND(json_extract(post, '$.ts') / 1000), 'unixepoch') AS date,
			COUNT(json_extract(post, '$.ts')) as postcount
        FROM posts
        GROUP BY date
        ORDER BY postcount DESC
        LIMIT 1)
    UNION
    SELECT
        12 as "Sort",
        "Posts" as "Category",
        "Most Active Poster in 1 Day" as "Statistic",
        GROUP_CONCAT(callsign || ': ' || postcount, ', ' ) as statistic
    FROM
        (SELECT  
            strftime('%d-%m-%Y', ROUND(json_extract(post, '$.ts')/1000), 'unixepoch') AS date,
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

    messages_select_query = f"""
    SELECT  
        2 as "Sort",
        "Messages" as "Category",
        "Total Messages" as "Statistic",
        COUNT(json_extract(message, '$.ts')) as count
    FROM messages
    UNION
    SELECT  
        3 as "Sort",
        "Messages" as "Category",
        "Messages Today So Far" as "Statistic",
        COUNT(json_extract(message, '$.ts')) as count
    FROM messages
    WHERE 
        CAST(json_extract(message, '$.ts') AS INTEGER) >= strftime('%s','now','localtime','start of day')
    UNION
    SELECT  
        6 as "Sort",
        "Messages" as "Category",
        "Total Messages Last 7 Days" as "Statistic",
        COUNT(json_extract(message, '$.ts')) as count
    FROM messages
    WHERE
        CAST(json_extract(message, '$.ts') AS INTEGER) >= strftime('%s','now','localtime','start of day','-7 days')
        AND CAST(json_extract(message, '$.ts') AS INTEGER) <  strftime('%s','now','localtime','start of day')
    UNION
    SELECT  
        7 as "Sort",
        "Messages" as "Category",
        "Total Messages Last 30 Days" as "Statistic",
        COUNT(json_extract(message, '$.ts')) as count
    FROM messages
    WHERE
        CAST(json_extract(message, '$.ts') AS INTEGER) >= strftime('%s','now','localtime','start of day','-30 days')
        AND CAST(json_extract(message, '$.ts') AS INTEGER) <  strftime('%s','now','localtime','start of day')
    UNION
    SELECT
        9 as "Sort",
        "Messages" as "Category",
        "Most Messages in 1 Day" as "Statistic",
        GROUP_CONCAT(date || ': ' || messagecount, ', ' ) as statistic
    FROM
        (SELECT  
            strftime('%d-%m-%Y', ROUND(json_extract(message, '$.ts')), 'unixepoch') AS date,
            COUNT(json_extract(message, '$.ts')) as messagecount
        FROM messages
        GROUP BY date
        ORDER BY messagecount DESC
        LIMIT 1)
    UNION
    SELECT
        11 as "Sort",
        "Messages" as "Category",
        "Most Active Messager in 1 Day" as "Statistic",
        GROUP_CONCAT(callsign || ': ' || messagecount, ', ' ) as statistic
    FROM
        (SELECT  
            strftime('%d-%m-%Y', ROUND(json_extract(message, '$.ts')), 'unixepoch') AS date,
            COUNT(json_extract(message, '$.ts')) as messagecount,
            json_extract(message, '$.fc') as callsign
        FROM messages
        GROUP BY date, callsign
        ORDER BY messagecount DESC
        LIMIT 1)
    ORDER BY 
        Category DESC, Sort ASC
    """

    server_select_query = f"""
    SELECT 
        1 as "Sort",
        "Server" as "Category",
        "Bytes Sent Today So Far" as "Statistic",
        IFNULL(sum(json_extract(event, '$.e.bytes')), 0) AS count
    FROM 
        events
    WHERE
        json_extract(event, '$.et') = "WPS_SEND" AND
        DATE(ROUND(json_extract(event, '$.ts')/1000), 'unixepoch') >= datetime('now', 'start of day')
    UNION
    SELECT 
        2 as "Sort",
        "Server" as "Category",
        "Bytes Sent Previous 7 Days" as "Statistic",
        sum(json_extract(event, '$.e.bytes')) AS count
    FROM 
        events
    WHERE
        json_extract(event, '$.et') = "WPS_SEND" AND
        CAST(json_extract(event, '$.ts') AS INTEGER) >= strftime('%s','now','localtime','start of day','-7 days') * 1000
        AND CAST(json_extract(event, '$.ts') AS INTEGER) <  strftime('%s','now','localtime','start of day') * 1000
    UNION
    SELECT 
        3 as "Sort",
        "Server" as "Category",
        "Bytes Sent Previous 30 Days" as "Statistic",
        sum(json_extract(event, '$.e.bytes')) AS count
    FROM 
        events
    WHERE
        json_extract(event, '$.et') = "WPS_SEND" AND
        CAST(json_extract(event, '$.ts') AS INTEGER) >= strftime('%s','now','localtime','start of day','-30 days') * 1000
        AND CAST(json_extract(event, '$.ts') AS INTEGER) <  strftime('%s','now','localtime','start of day') * 1000
    UNION
	SELECT  
		4 as "Sort",
		"Server" as "Category",
		"WPS Responses Sent Today So Far" as "Statistic",
		count(json_extract(event, '$.ts')) AS send_count
	FROM events
	WHERE
		json_extract(event, '$.et') = "WPS_SEND" AND
		DATE(ROUND(json_extract(event, '$.ts')/1000), 'unixepoch') >= datetime('now', 'start of day')
	UNION
    SELECT  
        5 as "Sort",
        "Server" as "Category",
        "WPS Responses Sent Previous 7 Days" as "Statistic",
        count(json_extract(event, '$.ts')) AS count
    FROM events
    WHERE
        json_extract(event, '$.et') = "WPS_SEND" AND
        CAST(json_extract(event, '$.ts') AS INTEGER) >= strftime('%s','now','localtime','start of day','-7 days') * 1000
        AND CAST(json_extract(event, '$.ts') AS INTEGER) <  strftime('%s','now','localtime','start of day') * 1000
    UNION
    SELECT  
        6 as "Sort",
        "Server" as "Category",
        "WPS responses sent previous 30 days" as "Statistic",
        count(json_extract(event, '$.ts')) AS count
    FROM events
    WHERE
        json_extract(event, '$.et') = "WPS_SEND" AND
        CAST(json_extract(event, '$.ts') AS INTEGER) >= strftime('%s','now','localtime','start of day','-30 days') * 1000
        AND CAST(json_extract(event, '$.ts') AS INTEGER) <  strftime('%s','now','localtime','start of day') * 1000
    ORDER BY
        Sort ASC
    """

    try:

        with sqlite3.connect(DB_FILENAME) as conn:
            cursor = conn.cursor()

            cursor.execute(unique_connecting_users_query)
            conn.commit()            
            
            for row in cursor:                
                result["h"]["uculsd"] = row[0]
            
            cursor.execute(posts_select_query)
            conn.commit()

            for row in cursor:                
                result["p"].append({ "s": row[2], "v": row[3] })

            cursor.execute(messages_select_query)
            conn.commit()

            for row in cursor:                
                result["m"].append({ "s": row[2], "v": row[3] })

        with sqlite3.connect(EVENTS_DB_FILENAME) as conn:
            cursor = conn.cursor()

            cursor.execute(server_select_query)
            conn.commit()

            for row in cursor:                
                result["s"].append({ "s": row[2], "v": row[3] })
        
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