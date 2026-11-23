#!/usr/bin/env node
// Отрисовать HTML-шаблон панели «МиниГен» ровно так, как это делает Node в
// рантайме: escape-последовательности внутри template literal превращаются в
// настоящие символы.
//
// ⚠️ Именно на этом ломалась панель 18.11: одиночный `\n` внутри шаблона
// стал настоящим переводом строки и разорвал строковый литерал в отданном
// скрипте. Проверять сырой текст шаблона бесполезно — он остаётся
// синтаксически валидным; ломается РЕЗУЛЬТАТ его вычисления.
//
// Вывод: распечатанный HTML на stdout. Код возврата 2 — шаблон не найден.

const fs = require('fs');
const path = require('path');

const OPEN = 'const HTML = `';
const TAIL = '</script></body></html>';

const file = process.argv[2] || path.join(__dirname, 'mcode-panel.js');
const src = fs.readFileSync(file, 'utf8');

const i = src.indexOf(OPEN);
if (i < 0) {
  console.error('шаблонная строка ' + OPEN + ' не найдена в ' + file);
  process.exit(2);
}
const start = i + OPEN.length;
const tail = src.indexOf(TAIL, start);
if (tail < 0) {
  console.error('хвост шаблона ' + TAIL + ' не найден');
  process.exit(2);
}
const end = src.indexOf('`', tail);
if (end < 0) {
  console.error('закрывающая кавычка шаблона не найдена');
  process.exit(2);
}

// Вычисляем шаблон так же, как это делает Node при загрузке модуля.
// `eval` здесь — сознательный приём: нам нужно именно ПОВЕДЕНИЕ template
// literal (escape-последовательности), а не её исходный текст.
let html;
try {
  html = eval('`' + src.slice(start, end) + '`');
} catch (err) {
  console.error('шаблон не вычисляется: ' + (err && err.message ? err.message : err));
  process.exit(3);
}

process.stdout.write(html);
