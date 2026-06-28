import argparse
from bot.stats import print_stats

def main():
    p=argparse.ArgumentParser(); p.add_argument("--file", default="trade_results.csv"); a=p.parse_args(); print_stats(a.file)
if __name__=="__main__": main()
