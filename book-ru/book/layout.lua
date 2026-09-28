-- Constrain graphics without scaling mathematical notation or table contents.
function Image(image)
  if image.src:match('%.pdf$') then
    return pandoc.RawInline('latex', '\\infragraphic{' .. image.src .. '}')
  end
  return image
end

function Math(math)
  -- Keep the Greek micro prefix in the math font, outside the Roman unit.
  math.text = math.text:gsub('\\mathrm{%s*\\mu%s+s%s*}', '\\mu\\,\\mathrm{s}')
  -- Wrap the two long prose equations at full font size, without editing manuscripts.
  if math.mathtype == 'DisplayMath' then
    local denominator = '\\text{число запросов или задач, удовлетворяющих требованиям к качеству и сроку}'
    local start, finish = math.text:find(denominator, 1, true)
    if start and math.text:find('C_{effective}', 1, true) then
      math.text = math.text:sub(1, start - 1)
        .. '\\begin{gathered}\\text{число запросов или задач,}\\\\\n'
        .. '\\text{удовлетворяющих требованиям к качеству и сроку}\\end{gathered}'
        .. math.text:sub(finish + 1)
    end
    local resource = '\\text{Потребность в ресурсе в единицу времени}='
      .. '\\text{скорость поступления задач}\\times'
      .. '\\text{средняя потребность в ресурсе на задачу}.'
    if math.text:match('^%s*(.-)%s*$') == resource then
      math.text = '\\begin{gathered}\\text{Потребность в ресурсе в единицу времени}=\\\\\n'
        .. '\\text{скорость поступления задач}\\\\\n'
        .. '\\times\\text{средняя потребность в ресурсе на задачу}.\\end{gathered}'
    end
  end
  -- Keep chapter 4's three energy terms at full size on separate lines.
  if math.mathtype == 'DisplayMath'
    and math.text:find('E_{\\mathrm{weights}}', 1, true)
    and math.text:find('E_{\\mathrm{KV}}', 1, true)
    and math.text:find('E_{\\mathrm{compute}}', 1, true) then
    local wrapped, count = math.text:gsub('\\quad%s*', '\\\\\n')
    if count == 2 then
      wrapped = wrapped:gsub('^%s+', ''):gsub('%s+$', '')
      math.text = '\\begin{gathered}\n' .. wrapped .. '\n\\end{gathered}'
    end
  end
  return math
end

function Table(table)
  -- The slash joins two Russian words; allow a line break without changing either.
  table = table:walk({Str = function(str)
    if str.text == 'слоёв/вызовов' then
      return {pandoc.Str('слоёв/'), pandoc.RawInline('latex', '\\allowbreak{}'), pandoc.Str('вызовов')}
    end
  end})
  -- TeX does not hyphenate the first word of a paragraph without a leading node.
  local function allow_first_word_hyphenation(block)
    block.content:insert(1, pandoc.RawInline('latex', '\\hspace{0pt}'))
    return block
  end
  table = table:walk({Plain = allow_first_word_hyphenation, Para = allow_first_word_hyphenation})
  local count = #table.colspecs
  if count > 0 then
    local heading = ''
    for _, row in ipairs(table.head.rows) do
      for _, cell in ipairs(row.cells) do
        heading = heading .. ' ' .. pandoc.utils.stringify(cell.contents)
      end
    end
    local widths = nil
    if count == 2 and heading:find('Время %(ns%)') then
      widths = {0.68, 0.28}
    elseif count == 4 and heading:find('RTX 4090', 1, true) then
      widths = {0.38, 0.19, 0.19, 0.20}
    elseif count == 3 and heading:find('Предположение', 1, true) then
      widths = {0.31, 0.52, 0.13}
    elseif count == 3 and heading:find('Требования модели', 1, true) then
      widths = {0.31, 0.23, 0.42}
    elseif count == 5 and heading:find('Модуль и назначение', 1, true) then
      widths = {0.24, 0.20, 0.20, 0.20, 0.12}
    elseif count == 6 and heading:find('Параметр архитектуры', 1, true) then
      widths = {0.26, 0.14, 0.14, 0.14, 0.14, 0.14}
    elseif count == 7 and heading:find('Маршрутизируемые эксперты', 1, true) then
      widths = {0.18, 0.16, 0.14, 0.13, 0.20, 0.08, 0.07}
    end
    for i, spec in ipairs(table.colspecs) do
      spec[1] = pandoc.AlignLeft
      spec[2] = widths and widths[i] or 0.96 / count
    end
    -- These measured replay / specialization tables need a fresh page after
    -- the preceding discussion and figure; otherwise XeLaTeX overfills it.
    if count == 3 and heading:find('Фактическое наблюдение', 1, true) then
      return {pandoc.RawBlock('latex', '\\clearpage'), table}
    elseif count == 4 and heading:find('Однократная подготовка P', 1, true) then
      return {
        pandoc.RawBlock('latex', '\\clearpage'),
        table,
        -- Keep the following comparison figure below its introductory text.
        pandoc.RawBlock('latex', '\\suppressfloats[t]')
      }
    elseif count == 5 and heading:find('Хранение и вычисления', 1, true) then
      return {
        pandoc.RawBlock('latex', '\\clearpage'),
        table,
        -- Keep the following token-time figure below its introductory text.
        pandoc.RawBlock('latex', '\\suppressfloats[t]')
      }
    end
  end
  return table
end

function Blocks(blocks)
  local result = pandoc.List()
  local i = 1
  while i <= #blocks do
    local block = blocks[i]
    local caption = block.t == 'Para' and pandoc.utils.stringify(block):match('^Таблица%s')
    local following = blocks[i + 1]
    if caption and following and following.t == 'Table' and #following.colspecs >= 6 then
      result:insert(pandoc.RawBlock('latex', '\\begin{landscape}\\setlength{\\columnwidth}{\\linewidth}'))
      result:insert(block)
      result:insert(following)
      result:insert(pandoc.RawBlock('latex', '\\end{landscape}'))
      i = i + 2
    elseif block.t == 'Table' and #block.colspecs >= 6 then
      result:insert(pandoc.RawBlock('latex', '\\begin{landscape}\\setlength{\\columnwidth}{\\linewidth}'))
      result:insert(block)
      result:insert(pandoc.RawBlock('latex', '\\end{landscape}'))
      i = i + 1
    else
      if caption then result:insert(pandoc.RawBlock('latex', '\\FloatBarrier')) end
      result:insert(block)
      if caption then result:insert(pandoc.RawBlock('latex', '\\nopagebreak[4]')) end
      i = i + 1
    end
  end
  return result
end
