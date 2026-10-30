'use strict';
// Identity is source-checked; every financial/price observation below is synthetic.
const IDENTITY_SOURCES={
 cxmt:'https://www.sse.com.cn/disclosure/announcement/listing/ipo/c/c_20260724_10826610.shtml',
 a500:'https://e.gtfund.com/Etrade/Jijin/etflist',
 n1000:'https://www.nffund.com/main/nffund/personal-financing/detail.shtml?fundCode=512100',
 efund:'https://cdn.efunds.com.cn/owch/data/bulletin/20260323/%E6%98%93%E6%96%B9%E8%BE%BE%E6%81%92%E7%94%9F%E7%A7%91%E6%8A%80%E4%BA%A4%E6%98%93%E5%9E%8B%E5%BC%80%E6%94%BE%E5%BC%8F%E6%8C%87%E6%95%B0%E8%AF%81%E5%88%B8%E6%8A%95%E8%B5%84%E5%9F%BA%E9%87%91%E8%81%94%E6%8E%A5%E5%9F%BA%E9%87%91%EF%BC%88QDII%EF%BC%89%E5%9F%BA%E9%87%91%E7%BB%8F%E7%90%86%E5%8F%98%E6%9B%B4%E5%85%AC%E5%91%8A.pdf?from=person',
 ai:'https://data.eastmoney.com/notices/detail/515980/AN202608131827948058.html'
};
const CATALOG=[
 {code:'002281',name:'光迅科技',type:'stock',industry:'光通信',watch:true,base:141,topic:'AI互联 / 光通信',questions:['订单与产品交付能否转化为收入？','新产品毛利率与客户集中度如何变化？','资本开支预期是否已被估值透支？']},
 {code:'601869',name:'长飞光纤',type:'stock',industry:'光纤光缆',watch:true,base:328,topic:'光纤光缆 / 通信基础设施',questions:['光纤价格与产能利用率是否改善？','海外收入与回款质量能否同步验证？','新业务投入与主业盈利如何拆分？']},
 {code:'688825',name:'长鑫科技',type:'stock',industry:'存储芯片',watch:true,base:48,topic:'存储 / DRAM',listed:'2026-07-27',shortHistory:true,source:IDENTITY_SOURCES.cxmt,questions:['存储价格与产品组合如何影响利润？','扩产折旧、利用率与现金支出是否匹配？','上市历史不足时，哪些指标应保持空缺？']},
 {code:'688981',name:'中芯国际',type:'stock',industry:'晶圆制造',watch:true,base:98,topic:'晶圆制造 / 产能周期',questions:['产能利用率与晶圆价格是否同步修复？','资本开支与折旧压力如何影响现金流？','工艺结构变化能否支撑盈利预期？']},
 {code:'300033',name:'同花顺',type:'stock',industry:'金融信息服务',watch:true,base:199,topic:'金融信息 / 市场活跃度',questions:['市场活跃度能否转化为持续付费？','产品收入与营销投入的关系如何？','现金创造与高增长预期是否匹配？']},
 {code:'300390',name:'天华新能',type:'stock',industry:'锂电材料',watch:true,base:22.8,topic:'锂电材料 / 资源周期',questions:['锂价与库存周期是否企稳？','经营现金流与盈利改善是否一致？','周期低基数是否放大表观增速？']},
 {code:'515980',name:'人工智能ETF华富',type:'etf',industry:'人工智能主题',watch:true,base:.866,topic:'人工智能',source:IDENTITY_SOURCES.ai,questions:['成分集中度与行业交叉暴露如何？','成分盈利与主题估值是否匹配？','份额申赎、折溢价与跟踪误差如何？']},
 {code:'588080',name:'科创50ETF易方达',shortName:'科创50ETF',type:'etf',industry:'科创宽基',watch:true,benchmark:true,base:1.414,topic:'科创50',manager:'易方达',source:IDENTITY_SOURCES.efund,questions:['成分结构与盈利质量如何变化？','成分估值和行业集中度是否上升？','份额变化与价格走势是否一致？']},
 {code:'510300',name:'沪深300ETF华泰柏瑞',shortName:'沪深300ETF',type:'etf',industry:'大盘宽基',benchmark:true,base:4.25,topic:'沪深300',manager:'华泰柏瑞'},
 {code:'159338',name:'中证A500ETF国泰',shortName:'A500ETF',type:'etf',industry:'全市场宽基',benchmark:true,base:1.12,topic:'中证A500',manager:'国泰',source:IDENTITY_SOURCES.a500},
 {code:'510500',name:'中证500ETF南方',shortName:'中证500ETF',type:'etf',industry:'中盘宽基',benchmark:true,base:6.8,topic:'中证500',manager:'南方'},
 {code:'512100',name:'中证1000ETF南方',shortName:'中证1000ETF',type:'etf',industry:'小盘宽基',benchmark:true,base:2.34,topic:'中证1000',manager:'南方',source:IDENTITY_SOURCES.n1000},
 {code:'159915',name:'创业板ETF易方达',shortName:'创业板ETF',type:'etf',industry:'成长宽基',benchmark:true,base:2.55,topic:'创业板',manager:'易方达',source:IDENTITY_SOURCES.efund}
];
const BENCH_CODES=['510300','159338','510500','512100','159915','588080'];
const WATCH_CODES=['002281','601869','688825','688981','300033','300390','515980','588080'];
const SKILL_MAP=[
 {name:'市场情绪偏离分析',id:'market-sentiment-gap',version:'安装版',pages:'情绪偏离 / 七维情绪',detail:'负面情绪、财务健康、竞争力、问题性质、催化剂与反证。',url:'https://chatgpt.com/skills?skill_id=6ae415e62d6481919824b65893ebcd7c'},
 {name:'科技炒作与基本面',id:'tech-hype-fundamentals',version:'安装版',pages:'估值与基本面 / 七维估值',detail:'企业阶段、估值口径、盈利质量、现金流、研发与概念收入兑现。',url:'https://chatgpt.com/skills?skill_id=6ae416045a888191ae42d64503e3fc4a'},
 {name:'量化因子选股',id:'ashare-factor-screening',version:'安装版',pages:'多因子筛选 / 个股画像',detail:'价值、动量、质量、低波动、规模、成长；子指标截面排名与权重。',url:'https://chatgpt.com/skills?skill_id=6ae41617f7408191a84fa00a9b1ba249'},
 {name:'笑哭脸K线信号',id:'smile-cry-limit-v2',version:'v2.2',pages:'信号实验室 / 市场监控',detail:'三重EMA、SHORTL/MIDL、买卖双参数、信号分级与三因子增强。',url:'https://chatgpt.com/skills?skill_id=6adc8e9e6924819195111b6592d986cf'}
];
const DIMENSIONS=[
 {name:'市场与行业',title:'把公司放回行业周期',fields:[['相对市场强弱','与沪深300ETF在相同区间对照。'],['行业景气证据','订单、价格、利用率与资本开支。'],['外围传导','从海外事件映射到产品、客户和收入。']],invalidate:'板块持续跑输、景气证据下修，或核心需求发生结构性变化。',page:'market'},
 {name:'基本面与盈利',title:'增长是否转化为现金',fields:[['收入与盈利','区分主营、非经常性损益和低基数。'],['现金与偿债','经营现金流、库存、应收与有息负债。'],['竞争力','客户、产品、毛利率与研发投入。']],invalidate:'经营现金流与盈利持续背离，或者核心竞争优势实质削弱。',page:'valuation'},
 {name:'估值与预期',title:'价格隐含了什么增长',fields:[['估值适配','盈利企业看PE，亏损或早期企业补充PS/EV口径。'],['预期敏感性','对盈利与估值倍数分别设情景，不编造目标价概率。'],['可比样本','公司阶段、业务与会计口径需要匹配。']],invalidate:'盈利预期下修，而估值仍依赖更高的增长假设。',page:'valuation'},
 {name:'资金与筹码',title:'区分交易分类与真实持仓',fields:[['资金统计','供应商大单分类不是机构身份。'],['杠杆与换手','融资余额、净买入与换手分位交叉验证。'],['ETF份额','份额变化、估算申赎与成交额分别显示。']],invalidate:'价格、资金与杠杆观察同时走弱；数据缺失时先核实。',page:'market'},
 {name:'技术与量价',title:'信号形成与成交分开记录',fields:[['趋势与波动','MA10/20/60、回撤、量比与波动。'],['笑哭脸','买入N=3/3、卖出N=5/5，保留信号解释。'],['历史检验','统一费用、时序和未成交口径后比较。']],invalidate:'触发原定失效条件，不等待某一种技术信号。',page:'backtest'},
 {name:'情绪与热度',title:'下跌是情绪，还是基本面变化',fields:[['情绪负面度','六个月表现、盈利修正、杠杆与舆情。'],['现实对照','用财务与行业证据核实市场叙事。'],['问题分类','暂时性、结构性、不确定，必须保留反证。']],invalidate:'把“跌得多”当作低估证据，或者无法排除结构性问题。',page:'sentiment'},
 {name:'事件与治理',title:'每个事件保留日期与适用范围',fields:[['公司披露','财报、业绩预告、减持解禁、审计与问询。'],['供应链变化','限制条款、客户变化与替代路线。'],['治理与兑现','回购、激励、关联交易与承诺执行。']],invalidate:'财务真实性、治理或核心业务可持续性受到重大影响。',page:'review'}
];
const FACTOR_DEFS=[
 {id:'value',name:'价值',parts:[['ebitEv',.3,1,'EBIT / EV'],['invPb',.2,1,'PB倒数'],['fcfEv',.3,1,'FCF / EV'],['ebitdaEv',.2,1,'EBITDA / EV']]},
 {id:'momentum',name:'动量',parts:[['momentum12',.5,1,'12–1月价格动量'],['epsRevision',.3,1,'3月EPS修正'],['turnover3m',.2,-1,'3月平均换手']]},
 {id:'quality',name:'质量',parts:[['roe',.3,1,'ROE'],['epsStd',.2,-1,'EPS增长波动'],['debt',.25,-1,'有息负债 / 资产'],['cashRatio',.25,1,'经营现金流 / 净利润']]},
 {id:'lowvol',name:'低波',parts:[['vol252',.4,-1,'一年实现波动率'],['beta104',.3,-1,'两年周频Beta'],['downside',.3,-1,'下行偏差']]},
 {id:'size',name:'规模',parts:[['marketCap',1,-1,'总市值']]},
 {id:'growth',name:'成长',parts:[['revenueCagr',.3,1,'三年收入CAGR'],['profitCagr',.3,1,'三年利润CAGR'],['marginDelta',.2,1,'三年利润率变化'],['forwardRevenue',.2,1,'预测收入增速']]}
];
const OVERSEAS=[['SOX / 纳指100','全球半导体与科技风险偏好','观察趋势、波动和相对强弱，不把隔夜涨跌机械映射为A股收益。'],['VIX / 美债实际收益率','海外压力与估值环境','区分预期波动、利率水平和变化速度，均需注明时点。'],['USD/CNH / 恒生科技','汇率与亚洲时段反馈','关注变化速度、跨市场背离与具体公司传导路径。'],['台积电 / 美光 / 云厂商指引','制造、存储与算力需求','按产品和客户映射资本开支、库存、价格、交付与盈利。']];

CATALOG.forEach(a=>{a.questions??=["成分结构与行业集中度如何变化？","整体盈利与当前估值是否匹配？","份额、跟踪误差与流动性是否稳定？"]});
