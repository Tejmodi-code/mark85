local n = 0
request = function() n = n + 1; return wrk.format("POST", "/api/signup", {["Content-Type"]="application/json"}, '{"name":"u' .. os.time() .. '_' .. n .. '_' .. math.random(1,999999) .. '","password":"secret12"}') end
