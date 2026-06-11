-- Increase connect timeout to 10 seconds
SELECT http_set_curlopt('CURLOPT_CONNECTTIMEOUT', '6');

-- Increase overall request timeout to 30 seconds
SELECT http_set_curlopt('CURLOPT_TIMEOUT', '12000');