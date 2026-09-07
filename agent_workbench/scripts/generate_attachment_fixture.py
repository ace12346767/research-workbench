"""Development-only XLS fixture generator (requires xlwt; not shipped)."""
from pathlib import Path
import xlwt


target = Path(__file__).resolve().parents[1] / 'tests' / 'fixtures' / 'attachment.xls'
target.parent.mkdir(exist_ok=True)
book = xlwt.Workbook()
sheet = book.add_sheet('Legacy Budget')
sheet.write(0, 0, 'Legacy Excel evidence')
sheet.write(1, 0, 'Server')
sheet.write(1, 1, 120)
book.save(str(target))
