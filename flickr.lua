dofile("table_show.lua")
dofile("urlcode.lua")
JSON = (loadfile "JSON.lua")()

local item_type = os.getenv('item_type')
local item_value = os.getenv('item_value')
local item_dir = os.getenv('item_dir')
local warc_file_base = os.getenv('warc_file_base')

local url_count = 0
local tries = 0
local downloaded = {}
local addedtolist = {}
local abortgrab = false

local discovered_photos = {}
local users = {}
local found_user = false

for ignore in io.open("ignore-list", "r"):lines() do
  downloaded[ignore] = true
end

if item_type == "users" then
  users[item_value] = true
elseif item_type == "photos" then
  baseuser = string.match(item_value, "([^/]+)")
end

load_json_file = function(file)
  if file then
    return JSON:decode(file)
  else
    return nil
  end
end

read_file = function(file)
  if file then
    local f = assert(io.open(file))
    local data = f:read("*all")
    f:close()
    return data
  else
    return ""
  end
end

allowed = function(url, parenturl)
  if string.match(url, "'+")
      or string.match(url, "[<>\\%*%$;%^%[%],%(%){}]")
      or string.match(url, "&?giftPro$")
      or string.match(url, "^https?://y3%.analytics%.yahoo%.com")
      or string.match(url, "^https?://geo%.yahoo%.com")
      or string.match(url, "^https?://www%.facebook%.com")
      or string.match(url, "^https?://sb%.scorecardresearch%.com")
      or string.match(url, "^https?://www%.flickr%.com/services/oembed") then
    return false
  end

  local tested = {}
  for s in string.gmatch(url, "([^/]+)") do
    if tested[s] == nil then
      tested[s] = 0
    end
    if tested[s] == 6 then
      return false
    end
    tested[s] = tested[s] + 1
  end

  if item_type == "disco" then
    if string.match(url, "^https?://api%.flickr%.com/services/rest") then
      return true
    end
  elseif item_type == "user" then
    if string.match(url, "^https?://www%.flickr%.com/photos/[^/]+/albums/[0-9]+")
        or string.match(url, "^https?://www%.flickr%.com/photos/[^/]+/collections[^%?]*$")
        or string.match(url, "^https?://www%.flickr%.com/photos/[^/]+/favorites[^%?]*$")
        or string.match(url, "^https?://www%.flickr%.com/photos/[^/]+/galleries[^%?]*$")
        or string.match(url, "^https?://www%.flickr%.com/photos/[^/]+/page[^%?]*$")
        or string.match(url, "^https?://www%.flickr%.com/photos/[^/]+/sets[^%?]*$") then
      if string.match(url, "/$") and (downloaded[string.match(url, "(.+)/$")] or addedtolist[string.match(url, "(.+)/$")]) then
        return false
      end
      if string.match(url, "[^/]$") and (downloaded[url .. "/"] or addedtolist[url .. "/"]) then
        return false
      end
    end
    if string.match(url, "^https?://www%.flickr%.com/photos/.+")
        or string.match(url, "^https?://www%.flickr%.com/people/.+") then
      if users[string.match(url, "^https?://[^/]+/[^/]+/([%-_A-Za-z0-9@]+)")] then
        return true
      end
    end
    if string.match(url, "^https?://[^%.]+%.staticflickr%.com/[0-9]+/[0-9]+/[0-9]+_[0-9a-z_]+%.jpg")
        or string.match(url, "^https?://www%.flickr%.com/photos/[^/]+/[0-9]+/") then
      return false
    end
    if string.match(url, "^https?://[^%.]+%.staticflickr%.com/[0-9]+/?[0-9]*/buddyicons/[^%.]+%.jpg") then
      if parenturl ~= nil and string.match(parenturl, "^https?://www%.flickr%.com/photos/[^/]+/$") and not found_user then
        users[string.match(url, "^https?://[^/]+/[^/]+/[^/]+/[^/]+/([^%.]+)")] = true
        found_user = true
        return true
      end
      return false
    end
    for s in string.gmatch(url, "([%-_A-Za-z0-9@]+)") do
      if users[s] then
        return true
      end
    end
  elseif item_type == "photos" or item_type == "photoscc" then
    if string.match(url, "^https?://www%.flickr%.com/photos/[^/]+/with/[0-9]+/")
    or string.match(url, "^https?://www%.flickr%.com/photos/[^/]+/[0-9]+$") 
    or string.match(url, "^https?://www%.flickr%.com/photos/[^/]+/sets/")
    then
      return false
    end
    if string.match(url, "^https?://www%.flickr%.com/photos/")
    or string.match(url, "^https?://www%.flickr%.com/video_download%.gne")
    or string.match(url, "^https?://[^%.]+%.staticflickr%.com/")
    then
      for i in string.gmatch(url, "([0-9]+)") do
        if users[i] then
          return true
        end
      end
    end
  end
  
  return false
end

wget.callbacks.download_child_p = function(urlpos, parent, depth, start_url_parsed, iri, verdict, reason)
  local url = urlpos["url"]["url"]
  local html = urlpos["link_expect_html"]

  if string.match(url, "^https?://y3%.analytics%.yahoo%.com")
      or string.match(url, "^https?://geo%.yahoo%.com")
      or string.match(url, "^https?://www%.facebook%.com")
      or string.match(url, "^https?://sb%.scorecardresearch%.com")
      or string.match(url, "^https?://www%.flickr%.com/services/oembed") then
    return false
  end

  if item_type == "user" then
    if string.match(url, "^https?://[^%.]+%.staticflickr%.com/[0-9]+/[0-9]+/[0-9]+_[0-9a-z_]+%.jpg")
        or string.match(url, "^https?://[^%.]+%.staticflickr%.com/[0-9]+/?[0-9]*/buddyicons/[^%.]+%.jpg") then
      return false
    end
  end
  
  if (downloaded[url] ~= true and addedtolist[url] ~= true)
      and (allowed(url, parent["url"]) or html == 0) then
    addedtolist[url] = true
    return true
  end
  
  return false
end

wget.callbacks.get_urls = function(file, url, is_css, iri)
  local urls = {}
  local html = nil
  
  downloaded[url] = true

  local function check(urla)
    local origurl = url
    local url = string.match(urla, "^([^#]+)")
    local url_ = string.gsub(url, "&amp;", "&")
    if (downloaded[url_] ~= true and addedtolist[url_] ~= true)
       and allowed(url_, origurl) then
      table.insert(urls, { url=url_ })
      addedtolist[url_] = true
      addedtolist[url] = true
    end
  end

  local function checknewurl(newurl)
    if string.match(newurl, "^https?:////") then
      check(string.gsub(newurl, ":////", "://"))
    elseif string.match(newurl, "^https?://") then
      check(newurl)
    elseif string.match(newurl, "^https?:\\/\\?/") then
      check(string.gsub(newurl, "\\", ""))
    elseif string.match(newurl, "^\\/\\/") then
      check(string.match(url, "^(https?:)")..string.gsub(newurl, "\\", ""))
    elseif string.match(newurl, "^//") then
      check(string.match(url, "^(https?:)")..newurl)
    elseif string.match(newurl, "^\\/") then
      check(string.match(url, "^(https?://[^/]+)")..string.gsub(newurl, "\\", ""))
    elseif string.match(newurl, "^/") then
      check(string.match(url, "^(https?://[^/]+)")..newurl)
    end
  end

  local function checknewshorturl(newurl)
    if string.match(newurl, "^%?") then
      check(string.match(url, "^(https?://[^%?]+)")..newurl)
    elseif not (string.match(newurl, "^https?:\\?/\\?//?/?")
       or string.match(newurl, "^[/\\]")
       or string.match(newurl, "^[jJ]ava[sS]cript:")
       or string.match(newurl, "^[mM]ail[tT]o:")
       or string.match(newurl, "^vine:")
       or string.match(newurl, "^android%-app:")
       or string.match(newurl, "^ios%-app:")
       or string.match(newurl, "^%${")) then
      check(string.match(url, "^(https?://.+/)")..newurl)
    end
  end

  if string.match(url, "^https?://www%.flickr%.com/photos/[^/]+/[0-9]+/$") then
    users[string.match(url, "^https?://[^/]+/[^/]+/[^/]+/([0-9]+)/")] = true
  end

  if allowed(url, nil)
      and not (string.match(url, "^https?://[^/]*staticflickr%.com/")
               or string.match(url, "^https?://[^/]*cdn%.yimg%.com/")) then
    html = read_file(file)
    if string.match(html, "<h3>We're having some trouble displaying this photo at the moment%. Please try again%.</h3>") then
      print("Flickr is having problems!")
      abortgrab = true
    end
    if item_type == "disco" and string.match(url, "^https?://api%.flickr%.com/services/rest") then
      local json = load_json_file(html)
      if string.match(url, "&page=1&") then
        for i=1,json["photos"]["pages"] do
          check(string.gsub(url, "&page=[0-9]+", "&page=" .. tostring(i)))
        end
      end
      for _, photo in pairs(json["photos"]["photo"]) do
        discovered_photos[photo["id"]] = true
      end
      return urls
    end
    if string.match(html, '"sizes":{.-}}') then
      local sizes = load_json_file(string.match(html, '"sizes":({.-}})'))
      local largest = nil
      if sizes["o"] then
        largest = "o"
      end
      for size, data in pairs(sizes) do
        if largest == nil then
          largest = size
        else
          if data["width"] > sizes[largest]["width"] then
            largest = size
          end
        end
      end
      checknewurl(sizes[largest]["displayUrl"])
      checknewurl(sizes[largest]["url"])
    end
    for newurl in string.gmatch(string.gsub(html, "&quot;", '"'), '([^"]+)') do
      if not string.match(newurl, "^\\/\\/") then
        checknewurl(newurl)
      end
    end
    for newurl in string.gmatch(string.gsub(html, "&#039;", "'"), "([^']+)") do
      checknewurl(newurl)
    end
    for newurl in string.gmatch(html, ">%s*([^<%s]+)") do
      checknewurl(newurl)
    end
    for newurl in string.gmatch(html, "[^%-]href='([^']+)'") do
      checknewshorturl(newurl)
    end
    for newurl in string.gmatch(html, '[^%-]href="([^"]+)"') do
      checknewshorturl(newurl)
    end
    for newurl in string.gmatch(html, ":%s*url%(([^%)]+)%)") do
      checknewurl(newurl)
    end
  end

  return urls
end

wget.callbacks.httploop_result = function(url, err, http_stat)
  status_code = http_stat["statcode"]
  
  url_count = url_count + 1
  io.stdout:write(url_count .. "=" .. status_code .. " " .. url["url"] .. "  \n")
  io.stdout:flush()

  if (status_code >= 300 and status_code <= 399) then
    local newloc = string.match(http_stat["newloc"], "^([^#]+)")
    if string.match(newloc, "^//") then
      newloc = string.match(url["url"], "^(https?:)") .. string.match(newloc, "^//(.+)")
    elseif string.match(newloc, "^/") then
      newloc = string.match(url["url"], "^(https?://[^/]+)") .. newloc
    elseif not string.match(newloc, "^https?://") then
      newloc = string.match(url["url"], "^(https?://.+/)") .. newloc
    end
    if downloaded[newloc] == true or addedtolist[newloc] == true then
      return wget.actions.EXIT
    end
  end
  
  if (status_code >= 200 and status_code <= 399) then
    downloaded[url["url"]] = true
    downloaded[string.gsub(url["url"], "https?://", "http://")] = true
  end

  if abortgrab == true then
    io.stdout:write("ABORTING...\n")
    return wget.actions.ABORT
  end
  
  if status_code >= 500
      or (status_code >= 400 and status_code ~= 404)
      or status_code  == 0 then
    io.stdout:write("Server returned "..http_stat.statcode.." ("..err.."). Sleeping.\n")
    io.stdout:flush()
    local maxtries = 8
    if not allowed(url["url"], nil) then
        maxtries = 2
    end
    if tries > maxtries then
      io.stdout:write("\nI give up...\n")
      io.stdout:flush()
      tries = 0
      if allowed(url["url"], nil) then
        return wget.actions.ABORT
      else
        return wget.actions.EXIT
      end
    else
      os.execute("sleep " .. math.floor(math.pow(2, tries)))
      tries = tries + 1
      return wget.actions.CONTINUE
    end
  end

  tries = 0

  local sleep_time = 0

  if sleep_time > 0.001 then
    os.execute("sleep " .. sleep_time)
  end

  return wget.actions.NOTHING
end

wget.callbacks.finish = function(start_time, end_time, wall_time, numurls, total_downloaded_bytes, total_download_time)
  if item_type == "disco" then
    local file = io.open(item_dir .. '/' .. warc_file_base .. '_data.txt', 'w')
    for photo, _ in pairs(discovered_photos) do
      file:write("photo:" .. item_value .. "/" .. photo .. "\n")
    end
    file:close()
  end
end

wget.callbacks.before_exit = function(exit_status, exit_status_string)
  if abortgrab == true then
    return wget.exits.IO_FAIL
  end
  return exit_status
end
