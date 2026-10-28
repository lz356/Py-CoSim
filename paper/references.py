"""Reference list in Building Simulation style (author-year; first three authors + et al.).

All entries were checked against Crossref/publisher/OSTI records (see paper/README.md).
``cite`` labels follow the journal's in-text rule: three or more authors -> "et al.".
"""

REFS = {
    "ahn2023": ("Ahn et al. 2023",
                "Ahn KU, Kim DW, Cho HM, et al. (2023). Alternative approaches to HVAC control of Chat Generative "
                "Pre-Trained Transformer (ChatGPT) for autonomous building system operations. Buildings, 13: 2680."),
    "allegrini2015": ("Allegrini et al. 2015",
                      "Allegrini J, Orehounig K, Mavromatidis G, et al. (2015). A review of modelling approaches and "
                      "tools for the simulation of district-scale energy systems. Renewable and Sustainable Energy "
                      "Reviews, 52: 1391–1404."),
    "benne2024": ("Benne et al. 2024",
                  "Benne K, Shapinsky T, Petersen A (2024). Alfalfa virtual building service: Software engineering "
                  "best practices applied to runtime interaction with building energy models. Presentation at "
                  "IBPSA-USA SimBuild 2024 Conference, Denver, CO, USA. NREL/PR-5500-89738, National Renewable "
                  "Energy Laboratory."),
    "bernal2012": ("Bernal et al. 2012",
                   "Bernal W, Behl M, Nghiem TX, et al. (2012). MLE+: A tool for integrated design and deployment of "
                   "energy efficient building controls. In: Proceedings of the 4th ACM Workshop on Embedded Sensing "
                   "Systems for Energy-Efficiency in Buildings (BuildSys '12), Toronto, Canada, pp. 123–130."),
    "blochwitz2011": ("Blochwitz et al. 2011",
                      "Blochwitz T, Otter M, Arnold M, et al. (2011). The Functional Mockup Interface for tool "
                      "independent exchange of simulation models. In: Proceedings of the 8th International Modelica "
                      "Conference, Dresden, Germany, pp. 105–114."),
    "blum2021": ("Blum et al. 2021",
                 "Blum D, Arroyo J, Huang S, et al. (2021). Building optimization testing framework (BOPTEST) for "
                 "simulation-based benchmarking of control strategies in buildings. Journal of Building Performance "
                 "Simulation, 14: 586–610."),
    "bonnema2025": ("Bonnema et al. 2025",
                    "Bonnema E, Allen A, Lee E, et al. (2025). Flexible integration of diverse HVAC technologies in "
                    "EnergyPlus via Python-enabled workflows. Technical Report, NREL/TP-5500-85628, National "
                    "Renewable Energy Laboratory, USA."),
    "campoy2025": ("Campoy-Nieves et al. 2025",
                   "Campoy-Nieves A, Manjavacas A, Jiménez-Raboso J, et al. (2025). Sinergym – A virtual testbed for "
                   "building energy optimization with reinforcement learning. Energy and Buildings, 327: 115075."),
    "chen2018": ("Chen et al. 2018",
                 "Chen Y, Hong T, Luo X (2018). An agent-based stochastic occupancy simulator. Building Simulation, "
                 "11: 37–49."),
    "chinde2025": ("Chinde et al. 2025",
                   "Chinde V, Woldekidan K, Bonnema E, et al. (2025). Integration of a grey-box refrigerated case "
                   "model in EnergyPlus via Python plugin. Building Simulation, 18: 3119–3132."),
    "crawley2001": ("Crawley et al. 2001",
                    "Crawley DB, Lawrie LK, Winkelmann FC, et al. (2001). EnergyPlus: Creating a new-generation "
                    "building energy simulation program. Energy and Buildings, 33: 319–331."),
    "dai2025": ("Dai et al. 2025",
                "Dai X, Chen R, Guan S, et al. (2025). BuildingGym: An open-source toolbox for AI-based building "
                "energy management using reinforcement learning. Building Simulation, 18: 1909–1927."),
    "deru2011": ("Deru et al. 2011",
                 "Deru M, Field K, Studer D, et al. (2011). U.S. Department of Energy commercial reference building "
                 "models of the national building stock. Technical Report, NREL/TP-5500-46861, National Renewable "
                 "Energy Laboratory, USA."),
    "dobos2014": ("Dobos 2014",
                  "Dobos AP (2014). PVWatts version 5 manual. Technical Report, NREL/TP-6A20-62641, National "
                  "Renewable Energy Laboratory, USA."),
    "doe2022": ("DOE 2022",
                "DOE (2022). EnergyPlus Version 22.2 Input Output Reference: Group – Python Plugins / Python EMS. "
                "U.S. Department of Energy. Available at https://bigladdersoftware.com/epx/docs/22-2/"
                "input-output-reference/group-python-plugins.html. Accessed 27 Oct 2026."),
    "drgona2020": ("Drgoňa et al. 2020",
                   "Drgoňa J, Arroyo J, Cupeiro Figueroa I, et al. (2020). All you need to know about model "
                   "predictive control for buildings. Annual Reviews in Control, 50: 190–232."),
    "elkontar2020": ("El Kontar et al. 2020",
                     "El Kontar R, Polly B, Charan T, et al. (2020). URBANopt: An open-source software development "
                     "kit for community and urban district energy modeling. In: Proceedings of SimBuild 2020, "
                     "ASHRAE/IBPSA-USA Building Simulation Conference."),
    "fhwa2017": ("FHWA 2017",
                 "FHWA (2017). 2017 National Household Travel Survey. U.S. Department of Transportation, Federal "
                 "Highway Administration. Available at https://nhts.ornl.gov. Accessed 27 Oct 2026."),
    "gomes2018": ("Gomes et al. 2018",
                  "Gomes C, Thule C, Broman D, et al. (2018). Co-simulation: A survey. ACM Computing Surveys, 51: 49."),
    "hardy2024": ("Hardy et al. 2024",
                  "Hardy TD, Palmintier B, Top PL, et al. (2024). HELICS: A co-simulation framework for scalable "
                  "multi-domain modeling and analysis. IEEE Access, 12: 24325–24347."),
    "harris2020": ("Harris et al. 2020",
                   "Harris CR, Millman KJ, van der Walt SJ, et al. (2020). Array programming with NumPy. Nature, "
                   "585: 357–362."),
    "holmgren2018": ("Holmgren et al. 2018",
                     "Holmgren WF, Hansen CW, Mikofski MA (2018). pvlib python: A python package for modeling solar "
                     "energy systems. Journal of Open Source Software, 3: 884."),
    "hong2018": ("Hong et al. 2018",
                 "Hong T, Langevin J, Sun K (2018). Building simulation: Ten challenges. Building Simulation, 11: "
                 "871–898."),
    "hong2020": ("Hong et al. 2020",
                 "Hong T, Chen Y, Luo X, et al. (2020). Ten questions on urban building energy modeling. Building and "
                 "Environment, 168: 106508."),
    "hong2025": ("Hong et al. 2025",
                 "Hong T, Lee SH, Zhang W, et al. (2025). Informing electrification strategies of residential "
                 "neighborhoods with urban building energy modeling. Building Simulation, 18: 3–18."),
    "jiang2024": ("Jiang et al. 2024",
                  "Jiang G, Ma Z, Zhang L, et al. (2024). EPlus-LLM: A large language model-based computing platform "
                  "for automated building energy modeling. Applied Energy, 367: 123431."),
    "jimenez2021": ("Jiménez-Raboso et al. 2021",
                    "Jiménez-Raboso J, Campoy-Nieves A, Manjavacas-Lucas A, et al. (2021). Sinergym: A building "
                    "simulation and control framework for training reinforcement learning agents. In: Proceedings of "
                    "the 8th ACM International Conference on Systems for Energy-Efficient Buildings, Cities, and "
                    "Transportation (BuildSys '21), Coimbra, Portugal, pp. 319–323."),
    "kahn1962": ("Kahn 1962", "Kahn AB (1962). Topological sorting of large networks. Communications of the ACM, 5: "
                              "558–562."),
    "kubler2000": ("Kübler and Schiehlen 2000",
                   "Kübler R, Schiehlen W (2000). Two methods of simulator coupling. Mathematical and Computer "
                   "Modelling of Dynamical Systems, 6: 93–113."),
    "li2025": ("Li and Hong 2025",
               "Li H, Hong T (2025). A digital twin platform for building performance monitoring and optimization: "
               "Performance simulation and case studies. Building Simulation, 18: 1561–1579."),
    "liu2025llm": ("Liu et al. 2025",
                   "Liu M, Zhang L, Chen J, et al. (2025). Large language models for building energy applications: "
                   "Opportunities and challenges. Building Simulation, 18: 225–234."),
    "liudong2025": ("Liu and Dong 2025",
                    "Liu Y, Dong B (2025). Urban scale vehicle-to-building-to-grid integration leveraging human "
                    "mobility modeling for enhanced grid flexibility. Building Simulation, 18: 3069–3095."),
    "moriyama2018": ("Moriyama et al. 2018",
                     "Moriyama T, De Magistris G, Tatsubori M, et al. (2018). Reinforcement learning testbed for "
                     "power-consumption optimization. In: Li L, Hasegawa K, Tanaka S (eds), Methods and Applications "
                     "for Modeling and Simulation of Complex Systems (AsiaSim 2018). Communications in Computer and "
                     "Information Science, Vol. 946. Singapore: Springer. pp. 45–59."),
    "neukomm2019": ("Neukomm et al. 2019",
                    "Neukomm M, Nubbe V, Fares R (2019). Grid-interactive efficient buildings technical report "
                    "series: Overview of research challenges and gaps. Technical Report, NREL/TP-5500-75470, "
                    "National Renewable Energy Laboratory, USA."),
    "nouidui2014": ("Nouidui et al. 2014",
                    "Nouidui TS, Wetter M, Zuo W (2014). Functional mock-up unit for co-simulation import in "
                    "EnergyPlus. Journal of Building Performance Simulation, 7: 192–202."),
    "nweye2025": ("Nweye et al. 2025",
                  "Nweye K, Kaspar K, Buscemi G, et al. (2025). CityLearn v2: Energy-flexible, resilient, "
                  "occupant-centric, and carbon-aware management of grid-interactive communities. Journal of Building "
                  "Performance Simulation, 18: 17–38."),
    "page2008": ("Page et al. 2008",
                 "Page J, Robinson D, Morel N, et al. (2008). A generalised stochastic model for the simulation of "
                 "occupant presence. Energy and Buildings, 40: 83–98."),
    "palmintier2017": ("Palmintier et al. 2017",
                       "Palmintier B, Krishnamurthy D, Top P, et al. (2017). Design of the HELICS high-performance "
                       "transmission-distribution-communication-market co-simulation framework. In: Proceedings of "
                       "2017 Workshop on Modeling and Simulation of Cyber-Physical Energy Systems (MSCPES), pp. 1–6."),
    "reinhart2016": ("Reinhart and Cerezo Davila 2016",
                     "Reinhart CF, Cerezo Davila C (2016). Urban building energy modeling – A review of a nascent "
                     "field. Building and Environment, 97: 196–202."),
    "scharnhorst2021": ("Scharnhorst et al. 2021",
                        "Scharnhorst P, Schubnel B, Fernández Bandera C, et al. (2021). Energym: A building model "
                        "library for controller benchmarking. Applied Sciences, 11: 3518."),
    "steinbrink2019": ("Steinbrink et al. 2019",
                       "Steinbrink C, Blank-Babazadeh M, El-Ama A, et al. (2019). CPES testing with mosaik: "
                       "Co-simulation planning, execution and analysis. Applied Sciences, 9: 923."),
    "towers2025": ("Towers et al. 2025",
                   "Towers M, Kwiatkowski A, Balis J, et al. (2025). Gymnasium: A standard interface for reinforcement "
                   "learning environments. In: Advances in Neural Information Processing Systems 38 (NeurIPS 2025), "
                   "Datasets and Benchmarks Track."),
    "trcka2009": ("Trčka et al. 2009",
                  "Trčka M, Hensen JLM, Wetter M (2009). Co-simulation of innovative integrated HVAC systems in "
                  "buildings. Journal of Building Performance Simulation, 2: 209–230."),
    "vazquez2019": ("Vázquez-Canteli et al. 2019",
                    "Vázquez-Canteli JR, Kämpf J, Henze G, et al. (2019). CityLearn v1.0: An OpenAI gym environment "
                    "for demand response with deep reinforcement learning. In: Proceedings of the 6th ACM "
                    "International Conference on Systems for Energy-Efficient Buildings, Cities, and Transportation "
                    "(BuildSys '19), New York, NY, USA, pp. 356–357."),
    "wang2020": ("Wang and Hong 2020",
                 "Wang Z, Hong T (2020). Reinforcement learning for building controls: The opportunities and "
                 "challenges. Applied Energy, 269: 115036."),
    "wetter2011": ("Wetter 2011",
                   "Wetter M (2011). Co-simulation of building energy and control systems with the Building Controls "
                   "Virtual Test Bed. Journal of Building Performance Simulation, 4: 185–203."),
    "wetter2014": ("Wetter et al. 2014",
                   "Wetter M, Zuo W, Nouidui TS, et al. (2014). Modelica Buildings library. Journal of Building "
                   "Performance Simulation, 7: 253–270."),
    "wetter2020": ("Wetter et al. 2020",
                   "Wetter M, Benne K, Gautier A, et al. (2020). Lifting the garage door on Spawn, an open-source "
                   "BEM-controls engine. In: Proceedings of SimBuild 2020, ASHRAE/IBPSA-USA Building Simulation "
                   "Conference."),
    "yan2015": ("Yan et al. 2015",
                "Yan D, O'Brien W, Hong T, et al. (2015). Occupant behavior modeling for building performance "
                "simulation: Current state and future challenges. Energy and Buildings, 107: 264–278."),
    "zhang2018": ("Zhang and Lam 2018",
                  "Zhang Z, Lam KP (2018). Practical implementation and evaluation of deep reinforcement learning "
                  "control for a radiant heating system. In: Proceedings of the 5th Conference on Systems for Built "
                  "Environments (BuildSys '18), Shenzhen, China, pp. 148–157."),
    "zhang2020cobs": ("Zhang and Ardakanian 2020",
                      "Zhang T, Ardakanian O (2020). COBS: COmprehensive Building Simulator. In: Proceedings of the "
                      "7th ACM International Conference on Systems for Energy-Efficient Buildings, Cities, and "
                      "Transportation (BuildSys '20), pp. 314–315."),
    "zhang2021dpc": ("Zhang 2021",
                     "Zhang L (2021). Data-driven building energy modeling with feature selection and active learning "
                     "for data predictive control. Energy and Buildings, 252: 111436."),
    "zhang2021review": ("Zhang et al. 2021",
                        "Zhang L, Wen J, Li Y, et al. (2021). A review of machine learning in building load "
                        "prediction. Applied Energy, 285: 116452."),
    "zhang2023": ("Zhang et al. 2023",
                  "Zhang L, Haroon SM, Ryan A (2023). Py-CoSim: Python-based building energy co-simulation "
                  "infrastructure. SSRN preprint, https://doi.org/10.2139/ssrn.4572925."),
    "zhangchen2024": ("Zhang and Chen 2024",
                      "Zhang L, Chen Z (2024). Large language model-based interpretable machine learning control in "
                      "building energy systems. Energy and Buildings, 313: 114278."),
    "zhu2025": ("Zhu and Li 2025",
                "Zhu X, Li H (2025). Heating, ventilation, and air conditioning (HVAC) temperature and humidity "
                "control optimization based on large language models (LLMs). Energies, 18: 1813."),
}
