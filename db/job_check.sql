SELECT *
FROM (
SELECT a.command, a.status, a.return_message, a.count::NUMERIC / b.command_cnt::NUMERIC AS rate
FROM (
SELECT command, status, return_message, COUNT(1) 
FROM cron.job_run_details
GROUP BY command, status, return_message
) AS a
LEFT JOIN (
	SELECT command, COUNT(1) AS command_cnt 
FROM cron.job_run_details
GROUP BY command

) AS b
ON a.command = b.command
WHERE status = 'failed'  

)
ORDER BY rate DESC;