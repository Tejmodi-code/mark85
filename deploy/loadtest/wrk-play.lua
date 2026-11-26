local n = 0
local token = "Bearer PASTE_A_TOKEN_HERE"
request = function()
  n = n + 1
  local key = "wrk-" .. os.time() .. "-" .. n .. "-" .. math.random(100000, 999999)
  return wrk.format("POST", "/api/play", {
    ["Content-Type"] = "application/json",
    ["Authorization"] = token,
    ["Idempotency-Key"] = key
  }, '{"game":"flip","bet":1,"choice":"H"}')
end
