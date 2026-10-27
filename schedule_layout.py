"""Generate period slots and visible break columns from school day boundaries.

Times are 24-hour HH:MM. All configured breaks are non-teaching intervals.
"""
from itertools import product

BREAK_NAMES = ('Short Break 1', 'Lunch', 'Short Break 2')

def clock(value):
    try:
        h, m = map(int, str(value).strip().split(':'))
        if 0 <= h <= 23 and 0 <= m <= 59:
            return 60*h+m
    except (TypeError, ValueError):
        pass
    raise ValueError('Use 24-hour time HH:MM, e.g. 09:00 or 15:30.')

def fmt(n):
    return f'{n//60:02d}:{n%60:02d}'

def make_periods(start, end, break1, lunch, break2, period_count):
    start, end = clock(start), clock(end)
    intervals = [(clock(a),clock(b)) for a,b in (break1,lunch,break2)]
    count = int(period_count)
    if not 1 <= count <= 12: raise ValueError('Choose 1 to 12 periods.')
    if not 360 <= start < end <= 1320: raise ValueError('School hours must be 06:00–22:00.')
    previous = start
    for label,(a,b) in zip(BREAK_NAMES,intervals):
        if not previous < a < b < end:
            raise ValueError(f'{label} must start after the previous interval and end before school finishes.')
        previous = b
    bounds=[(start,intervals[0][0]),(intervals[0][1],intervals[1][0]),
            (intervals[1][1],intervals[2][0]),(intervals[2][1],end)]
    lengths=[b-a for a,b in bounds]
    if count < 4: raise ValueError('At least 4 periods are needed with 3 separate breaks.')
    # Find a period distribution that keeps teaching periods as evenly sized as possible.
    target=sum(lengths)/count
    candidates=(counts for counts in product(range(1,count-2),repeat=4) if sum(counts)==count)
    allocation=min(candidates,key=lambda nums:sum(n*((length/n)-target)**2 for n,length in zip(nums,lengths)))
    periods=[]
    for (a,b),n in zip(bounds,allocation):
        if b-a < n*15: raise ValueError('Not enough teaching minutes: periods must be at least 15 minutes.')
        for i in range(n):
            left=a+(b-a)*i//n
            right=a+(b-a)*(i+1)//n
            periods.append((fmt(left),fmt(right)))
    return periods

def columns(periods, breaks=None):
    """Return display columns as (label, start, end, period_index|None)."""
    result=[]
    for i,(a,b) in enumerate(periods):
        result.append((f'P{i+1}',a,b,i))
        if i+1 < len(periods) and clock(b)<clock(periods[i+1][0]):
            gap_start,gap_end=b,periods[i+1][0]
            if breaks:
                name=next((label for label,(x,y) in breaks.items() if x==gap_start and y==gap_end),None)
            else:
                name=None
            if name is None:
                existing=sum(1 for x in result if x[3] is None)
                name=('Short Break 1','Lunch','Short Break 2')[min(existing,2)]
            result.append((name,gap_start,gap_end,None))
    return result

def subject_counts(slots):
    from collections import Counter
    return Counter(slot.subject for slot in slots.values() if slot.subject)
