"""Market intelligence: order books, order flow, price structure, options, events and replay.

Every module here reads normalized market data (`jdquant.marketdata.book`) and produces observations,
never orders. Strategies may use those observations as signals, and the risk engine and OMS stay the
only path to a broker.
"""
