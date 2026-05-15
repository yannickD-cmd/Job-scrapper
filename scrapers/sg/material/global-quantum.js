/**
 * mappings d'indexation pour les contenus "Job Offers" :
 * sitemap:loc	docid	"<collection_name>|sitemap:loc"
 * section <h1>	title
 * sitemap:loc	url1
 * og:image	url2
 * og:locale	documentlanguages	'fr' | 'en'
 * Reference	sourcestr4
 * 	sourcestr6	'job'
 * Location	sourcestr7
 * Location sourcecsv1 <City>;<Country> pour recherche par pays ou ville
 * Contract Type EN     sourcestr8             English label
 * Contract Type FR    sourcestr9              French label
 * Job Function EN     sourcestr10             English label
 * Job Function FR     sourcestr11             French label
 * Business Unit    sourcestr15             French label
 * Full ID     sourcestr12     <sourcestr4>-<documentlanguages>
 * og:description      sourcestr13
 * Publication Date	sourcedatetime1	Pattern : yyyy-MM-dd 00:00:00
 *
 * mappings d'indexation pour les contenus "Pages" :
 * Careers Field	CES Fields	Value(s)	Commentaires
 * sitemap:loc	docid	"<collection_name>|sitemap:loc"
 * section <h1>	title
 * sitemap:loc	url1
 * og:image	url2
 * og:locale	documentlanguages	'fr' | 'en'
 * sourcestr6	'page'	Custom CES
 * og:description 	sourcestr13
 * Page Description 	sourcestr14 	  	1st page paragraph
 **/
;(function (W, D, $) {
  const LANG = document.documentElement.lang;
  const ENV = document.getElementsByTagName("html")[0].getAttribute("data-env") === 'prod' ? 'prod' : 'preprod';
  const DEFAULT_DATA = drupalSettings.quantum.default_data[LANG];
  const LAST_OFFERS = drupalSettings.quantum.last_offers;
  const ALL_OFFERS = drupalSettings.quantum.all_offers;
  const OFFERS_FILTERS = drupalSettings.quantum.quantum_filters;
  const BASE_URL = drupalSettings.quantum.base_url; // SETTINGS.PHP > quantum_base_url

  const AUTH_URL = "https://sso.sgmarkets.com/sgconnect/oauth2/access_token";
  const PROXY_URL = W.location.origin + '/search-proxy.php';
  const PROFILE_URL = BASE_URL + '/search-profile';
  const SUGGESTIONS_URL = BASE_URL + '/search-suggest';

  const SKIPCOUNT_NB_DEFAULT = 10;
  const SKIPFROM_NB_DEFAULT = 0;
  const ONE_DAY = 24 * 60 * 60 * 1000;

  const FILTER_PAGE_LANG = "documentlanguages";
  const FILTER_API_TYPE_SEARCH = "sourcestr6";
  const FILTER_API_LOCATION_FULL = "sourcecsv1";
  const FILTER_API_LOCATION = "sourcestr7";
  const FILTER_API_CONTRACT_TYPE = "sourcestr8";
  const FILTER_API_JOB_FAMILY = "sourcestr10";
  const FILTER_API_BUSINESS_UNIT = "sourcestr15";
  const FILTER_API_FULL_ID = "sourcestr12";
  const FILTER_API_OG_DESC = "sourcestr13";
  const FILTER_API_PAGE_FIRST_PARAGRAPH = "sourcestr14";
  const FILTER_API_HYBRID = "sourcebool1";

  const REFINEMENT_NAME_JOB_LOCATION = "job_location";
  const REFINEMENT_NAME_CONTRACT_TYPE = "contract_type";
  const REFINEMENT_NAME_JOB_FAMILY = "job_family";
  const REFINEMENT_NAME_BUSINESS_UNIT = "business_unit";

  let getTokenPromise = null;
  let throttleTimer,
    iThresoldNewJob = ONE_DAY * 3,
    iNbTryMax = 2;

  let sgc_quantum = W.sgc_quantum = {};
  sgc_quantum.proxy = (function () {
    const oMod = {
      /**
       *
       * @param url
       * @param init_options
       * @returns {Promise<Response>}
       */
      proxy_fetch: function (url, init_options) {
        init_options.headers['X-Proxy-URL'] = url;
        return fetch(PROXY_URL, init_options);
      }
    };
    return oMod;
  })();

  sgc_quantum.utils = (function () {
    let iPageCount = 0;
    const getLang = function () {
      return LANG.toLowerCase() || 'fr';
    };

    const getEnv = function () {
      return ENV || 'prod';
    };

    const getDefaultData = function () {
      return DEFAULT_DATA;
    };

    const getLastOffers = function () {
      return LAST_OFFERS;
    };

    const getAllOffers = function () {
      return ALL_OFFERS;
    };

    const getOffersFilters = function () {
      return OFFERS_FILTERS;
    };

    const getFilterApiJobLocation = function () {
      return FILTER_API_LOCATION_FULL;
    };

    const getFilterApiJobLocation1 = function () {
      return FILTER_API_LOCATION;
    };

    const getFilterApiBusinessUnit = function () {
      return FILTER_API_BUSINESS_UNIT;
    };

    const getFilterApiOgDesc = function () {
      return FILTER_API_OG_DESC;
    };

    const getFilterApiPageFirstParagraph = function () {
      return FILTER_API_PAGE_FIRST_PARAGRAPH;
    };

    const getFilterApiContractType = function () {
      return FILTER_API_CONTRACT_TYPE;
    };

    const getFilterApiJobFamily = function () {
      return FILTER_API_JOB_FAMILY;
    };

    const getFilterApiJobHybrid = function () {
      return FILTER_API_HYBRID;
    };

    const getJobContractType = function (oJob) {
      const contracts = this.getOffersFilters()['refContrat'][LANG];
      for (let contract in contracts) {
        if (contracts[contract].id === oJob[FILTER_API_CONTRACT_TYPE]) {
          return contract;
        }
      }
      return oJob[FILTER_API_CONTRACT_TYPE];
    };

    const getJobJobFamily = function (oJob) {

      if (typeof LANG === 'undefined' || LANG === 'fr') {
        return oJob[FILTER_API_JOB_FAMILY_FR];
      } else {
        return oJob[FILTER_API_JOB_FAMILY_EN];
      }
    };

    const getJobHybrid = function (oJob) {
      if (this.getFilterApiJobHybrid() in oJob) {
        return (oJob[this.getFilterApiJobHybrid()] === true) ? 1 : 0;
      }
      return 0;
    };

    const getJobLocation = function (oJob) {
      const locationsFormated = [];
      const locations = this.getOffersFilters()['refLocation'][LANG];
      const locationIds = oJob[FILTER_API_LOCATION].split(', ');
      locationIds.forEach((locationId) => {
        for (let location in locations) {
          if (locations[location].id === locationId) {
            locationsFormated.push(location);
          }
        }
      });
      if (locationsFormated.length > 0) {
        return locationsFormated.join(', ');
      }
      return oJob[FILTER_API_LOCATION];
    };

    const getJobLocationFromDb = function (oJob) {
      const locations = this.getOffersFilters()['refLocation']['id'];
      let locationTxt = '';
      if (ALL_OFFERS[oJob.sourcestr4]) {
        if (ALL_OFFERS[oJob.sourcestr4]['city'] && locations[ALL_OFFERS[oJob.sourcestr4]['city']][LANG]) {
          locationTxt = locations[ALL_OFFERS[oJob.sourcestr4]['city']][LANG];
        }
        if (ALL_OFFERS[oJob.sourcestr4]['region'] && locations[ALL_OFFERS[oJob.sourcestr4]['region']][LANG]) {
          locationTxt += ((locationTxt.length) ? ', ' : '') + locations[ALL_OFFERS[oJob.sourcestr4]['region']][LANG];
        }
        if (ALL_OFFERS[oJob.sourcestr4]['country'] && locations[ALL_OFFERS[oJob.sourcestr4]['country']][LANG]) {
          locationTxt += ((locationTxt.length) ? ', ' : '') + locations[ALL_OFFERS[oJob.sourcestr4]['country']][LANG];
        }
      }
      return (locationTxt.length) ? locationTxt : this.getJobLocation(oJob);
    };

    const getJobFullId = function (oJob) {
      return oJob[FILTER_API_FULL_ID];
    };

    const getRefinementNameJobLocation = function () {
      return REFINEMENT_NAME_JOB_LOCATION;
    };

    const getRefinementNameContractType = function () {
      return REFINEMENT_NAME_CONTRACT_TYPE;
    };

    const getRefinementNameBusinessUnit = function () {
      return REFINEMENT_NAME_BUSINESS_UNIT;
    };

    const getRefinementNameJobFamily = function () {
      return REFINEMENT_NAME_JOB_FAMILY;
    };

    const isMobile = function () {
      return $(window).width() <= 700; /*TODO improvment: stock it and change it on resize etc. */
    };

    const ignoredKeyCodes = function() {
      return [
        37, // left
        39, // right
        38, // up
        40, // down
        16, // shift
        20, // caps
        17, // ctrl
        18, // alt
        33, // pageup
        34, // pagedown
        36, // home
        35, // end
        45, // insert
        9, // tab
        19, //pause
        112, // F1
        113, // F2
        114, // ...
        115, // ...
        116, // ...
        117, // ...
        118, // ...
        119, // ...
        120, // ...
        121, // ...
        122, // ...
        123, // F12
        44, // F13
        144, // numlock
        145, // scrolllock
        0 // fn
      ];
    };

    const objectsAreSame = function (x, y, list) {
      var same = true;

      if (x.length !== y.length || x.length === 0) {
        same = false;
      } else {
        if (list === "jobs") {
          for (var i = 0; i < x.length; i++) {
            if (x[i] !== y[i].path) {
              same = false;
            }
          }
        } else if (list === "suggestions") {
          for (var _i = 0; _i < x.length; _i++) {
            if (x[_i] !== y[_i].query) {
              same = false;
            }
          }
        } else if (list === "pages") {
          for (var _i2 = 0; _i2 < x.length; _i2++) {
            if (x[_i2] !== y[_i2].path) {
              same = false;
            }
          }
        }
      }

      return same;
    };

    const thumbUrl = function (e) {
      let str = e.substring(e.lastIndexOf("/") + 1, e.length);
      let last = str.slice(0, str.indexOf('.'));
      return "/sites/default/files/JobOfferThumb/" + last + "-thumb.jpg";
    };

    const addSeconds = function (date, seconds) {
      return new Date(date.getTime() + seconds * 1000);
    };

    const processResult = function (records) {
      if (!(records.TotalCount && records.TotalCount > 0)) {
        this.iPageCount = 0;
        return [];
      } else {
        this.iPageCount = records.PageCount;
        return records.Result.Docs;
      }
    };

    const highlightText = function ($text, $element) {
      $element.mark(_improveHighlightedText($text), {
        "ignoreJoiners": true,
        "wildcards": "withSpaces"
      });
    };

    const _improveHighlightedText = function ($text) {
      let aText = $text.split(" ");
      let $aWildcardedText = [];

      for (let index = 0; index < aText.length; index++) {
        let item = aText[index];
        $aWildcardedText.push(item)
        if (item.length > 4) {
          $aWildcardedText.push('??' + item.slice(2))
          $aWildcardedText.push(item.slice(0, -2) + '??')
        } else if (item.length > 2) {
          $aWildcardedText.push('?' + item.slice(1))
          $aWildcardedText.push(item.slice(0, -1) + '?')
        }
      }

      aText = $aWildcardedText.join(" ");
      return aText;
    };

    const slugify = function (str) {
      str = str.replace(/^\s+|\s+$/g, ''); // trim
      str = str.toLowerCase();

      // remove accents, swap ñ for n, etc
      var from = "àáäâèéëêìíïîòóöôùúüûñç·/_,:;";
      var to = "aaaaeeeeiiiioooouuuunc------";
      for (var i = 0, l = from.length; i < l; i++) {
        str = str.replace(new RegExp(from.charAt(i), 'g'), to.charAt(i));
      }

      str = str.replace(/[^a-z0-9 -]/g, '') // remove invalid chars
        .replace(/\s+/g, '-') // collapse whitespace and replace by -
        .replace(/-+/g, '-'); // collapse dashes

      return str;
    };

    const throttle = (callback, time, optionalParam) => {
      if (throttleTimer) return;
      throttleTimer = true;
      setTimeout(() => {
        typeof optionalParam !== 'undefined' ? callback(optionalParam) : callback() ;
        throttleTimer = false;
      }, time);
    };

    const isNewOffer = function (oJob) {
      let oOfferDate = oJob.sourcedatetime1
      if (typeof oOfferDate !== 'object') {
        oOfferDate = new Date(oOfferDate);
      }
      return ((new Date) - oOfferDate) < iThresoldNewJob;
    };

    const getCookie = function (name) {
      const nameEQ = name + "=";
      const cookies = document.cookie.split(';');
      for (let i = 0; i < cookies.length; i++) {
        let cookie = cookies[i].trim();
        if (cookie.indexOf(nameEQ) === 0) {
          return cookie.substring(nameEQ.length, cookie.length);
        }
      }
      return null;
    };

    const setCookie = function(name, value, days) {
      const date = new Date();
      date.setTime(date.getTime() + (days * 24 * 60 * 60 * 1000));
      const expires = "expires=" + date.toUTCString();
      document.cookie = `${name}=${value}; ${expires}; path=/; Secure`;
    };

    const oMod = {
      isMobile: isMobile,
      ignoredKeyCodes: ignoredKeyCodes,
      objectsAreSame: objectsAreSame,
      thumbUrl: thumbUrl,
      addSeconds: addSeconds,
      isNewOffer: isNewOffer,
      processResult: processResult,
      highlightText: highlightText,
      slugify: slugify,
      throttle: throttle,
      getLang: getLang,
      getEnv: getEnv,
      getDefaultData: getDefaultData,
      getLastOffers: getLastOffers,
      getAllOffers: getAllOffers,
      getOffersFilters: getOffersFilters,
      getRefinementNameJobLocation: getRefinementNameJobLocation,
      getRefinementNameJobFamily: getRefinementNameJobFamily,
      getRefinementNameContractType: getRefinementNameContractType,
      getRefinementNameBusinessUnit: getRefinementNameBusinessUnit,
      getFilterApiJobLocation: getFilterApiJobLocation,
      getFilterApiJobLocation1: getFilterApiJobLocation1,
      getFilterApiJobFamily: getFilterApiJobFamily,
      getFilterApiContractType: getFilterApiContractType,
      getFilterApiBusinessUnit: getFilterApiBusinessUnit,
      getFilterApiJobHybrid:getFilterApiJobHybrid,
      getJobContractType: getJobContractType,
      getJobJobFamily: getJobJobFamily,
      getJobLocation: getJobLocation,
      getJobLocationFromDb: getJobLocationFromDb,
      getJobFullId: getJobFullId,
      getJobHybrid: getJobHybrid,
      getFilterApiOgDesc: getFilterApiOgDesc,
      getFilterApiPageFirstParagraph: getFilterApiPageFirstParagraph,
      getCookie: getCookie,
      setCookie: setCookie,
    }

    return oMod;
  }());

  sgc_quantum.search_api = (function () {
    let iNbTry = 0;
    const sendRequest = function (queryBody, endpoint, callback) {
      sgc_quantum.auth_api.getToken().then(function (token) {
        iNbTry++;
        sgc_quantum.proxy.proxy_fetch(endpoint, {
          "method": "POST",
          "headers": {
            "Content-Type": "application/json",
            "Authorization-API": "Bearer " + token
          },
          "body": JSON.stringify(queryBody)
        }).then(function (response) {
          if (response.ok) {
            iNbTry = 0;
            return response.json();
          } else {
            if (response.status == 401 && iNbTry <= iNbTryMax) {
              sendRequest(queryBody, endpoint, callback);
            }
            throw new Error("API HTTP code : " + response.status);
          }
        }).then(function (json) {
          if (callback instanceof Function) {
            callback(json);
          }
        }).catch(function (err) {
          console.error(endpoint, err);
        });
      }).catch(function (err) {
        console.error("Impossible de récupérer le token :", err);
      });
    };

    const isValidTerm = function (term) {
      // TODO: improvement , test input
      if (term) {
        return true;
      }
    };

    const jobByIdSearch = function (aIds, callback) {
      if (aIds.length > 0) {
        const search_data = {
          "profile": "ces_profile_sgcareers",
          "query": {
            "advanced": [
              {
                "type": "simple",
                "name": FILTER_API_TYPE_SEARCH,
                "op": "eq",
                "value": "job"
              },
              {
                "type": "multi",
                "name": FILTER_API_FULL_ID,
                "op": "eq",
                "values": aIds
              },
            ],
            "skipCount": 1000,
          },
          "responseType": "SearchResult"
        };
        sendRequest(search_data, PROFILE_URL, callback);
      }
    }

    /**
     * Élargit les IDs de localisation pour inclure les alias.
     * Utilise le mapping locationAliases généré par PHP.
     * Ex: LUX_A01 (ville Luxembourg) a comme alias LUX (pays Luxembourg)
     * car ils ont le même nom traduit.
     */
    const expandLocationIds = function (locationIds) {
      const locationAliases = OFFERS_FILTERS['locationAliases'] || {};
      let expandedIds = [...locationIds];

      locationIds.forEach(function (id) {
        // Ajouter les alias de cet ID s'ils existent
        if (locationAliases[id]) {
          locationAliases[id].forEach(function (aliasId) {
            if (expandedIds.indexOf(aliasId) === -1) {
              expandedIds.push(aliasId);
            }
          });
        }
      });

      return expandedIds;
    };

    const jobSearch = function (search_term, array_search_refinements, callback, skipcount, skipfrom, filters = null) {
      let useLocation = false;
      let useContractType = false;
      let useFamily = false;
      if (filters == null || filters.match(/location/)) {
        useLocation = true;
      }
      if (filters == null || filters.match(/contract/)) {
        useContractType = true;
      }
      if (filters == null || filters.match(/family/)) {
        useFamily = true;
      }
      if (typeof skipcount === 'undefined') {
        skipcount = SKIPCOUNT_NB_DEFAULT;
      }

      if (typeof skipfrom === 'undefined') {
        skipfrom = SKIPFROM_NB_DEFAULT;
      }

      if (typeof search_term === 'undefined') {
        search_term = ''
      }

      if (search_term !== '' && !isValidTerm(search_term)) {
        return false;
      }

      const search_data = {
        "profile": "ces_profile_sgcareers",
        "query": {
          "advanced": [
            {
              "type": "simple",
              "name": FILTER_API_TYPE_SEARCH,
              "op": "eq",
              "value": "job"
            }
          ],
          "skipCount": skipcount,
          "skipFrom": skipfrom
        },
        "lang": LANG,
        "responseType": "SearchResult"
      };

      if (search_term !== '') {
        search_data.query['text'] = search_term;
        search_data.query['sort'] = 'globalrelevance.desc,sourcedatetime1.desc';
      }

      if (typeof array_search_refinements !== 'undefined' && !$.isEmptyObject(array_search_refinements)) {
        if (typeof array_search_refinements[REFINEMENT_NAME_JOB_LOCATION] !== 'undefined' && array_search_refinements[REFINEMENT_NAME_JOB_LOCATION] !== '') {
          if (useLocation) {
            let location = array_search_refinements[REFINEMENT_NAME_JOB_LOCATION].split(",");
            // Élargir les IDs de localisation pour inclure les variantes (ex: LUX_A01 -> ajouter LUX)
            location = expandLocationIds(location);
            search_data.query.advanced.push({
              "type": "multi",
              "name": FILTER_API_LOCATION_FULL,
              "op": "eq",
              "values": location
            })
          }
        }
        if (typeof array_search_refinements[REFINEMENT_NAME_CONTRACT_TYPE] !== 'undefined' && array_search_refinements[REFINEMENT_NAME_CONTRACT_TYPE] !== '') {
          if (useContractType) {
            let contract_type = array_search_refinements[REFINEMENT_NAME_CONTRACT_TYPE].split(",");
            search_data.query.advanced.push({
              "type": "multi",
              "name": sgc_quantum.utils.getFilterApiContractType(),
              "op": "eq",
              "values": contract_type
            })
          }
        }
        if (typeof array_search_refinements[REFINEMENT_NAME_JOB_FAMILY] !== 'undefined' && array_search_refinements[REFINEMENT_NAME_JOB_FAMILY] !== '') {
          if (useFamily) {
            let job_family = array_search_refinements[REFINEMENT_NAME_JOB_FAMILY].split(",");
            search_data.query.advanced.push({
              "type": "multi",
              "name": sgc_quantum.utils.getFilterApiJobFamily(),
              "op": "eq",
              "values": job_family
            })
          }
        }
        if (typeof array_search_refinements[REFINEMENT_NAME_BUSINESS_UNIT] !== 'undefined' && array_search_refinements[REFINEMENT_NAME_BUSINESS_UNIT] !== '') {
          let business_unit = array_search_refinements[REFINEMENT_NAME_BUSINESS_UNIT].split(",");
          search_data.query.advanced.push({
            "type": "multi",
            "name": sgc_quantum.utils.getFilterApiBusinessUnit(),
            "op": "eq",
            "values": business_unit
          })
        }
      }
      if (filters === null) {
        window.didomiOnReady = window.didomiOnReady || [];
        window.didomiOnReady.push(function (Didomi) {
          window.dataLayer = window.dataLayer || [];
          let event = {};
          event.event = 'search';
          event.search_term = search_term;
          event.search_page = ((skipfrom / skipcount) + 1);
          if (window.dataLayer) {
            window.dataLayer.push(event);
          }
        });
      }
      sendRequest(search_data, PROFILE_URL, callback);
    };

    const pageSearch = function (search_term, callback, skipcount, skipfrom) {
      if (typeof skipcount === 'undefined') {
        skipcount = SKIPCOUNT_NB_DEFAULT;
      }

      if (typeof skipfrom === 'undefined') {
        skipfrom = SKIPFROM_NB_DEFAULT;
      }

      if (typeof search_term === 'undefined') {
        search_term = ''
      }

      if (search_term !== '' && !isValidTerm(search_term)) {
        return false;
      }

      const search_data = {
        "profile": "ces_profile_sgcareers",
        "query": {
          "advanced": [
            {
              "type": "simple",
              "name": FILTER_API_TYPE_SEARCH,
              "op": "eq",
              "value": "page"
            },
            {
              "type": "simple",
              "name": FILTER_PAGE_LANG,
              "op": "eq",
              "value": LANG
            }
          ],
          "skipCount": skipcount,
          "skipFrom": skipfrom
        },
        "responseType": "SearchResult"
      };

      if (search_term !== '') {
        search_data.query['text'] = search_term;
      }

      sendRequest(search_data, PROFILE_URL, callback);
    };

    const suggestionSearch = function (search_term, callback) {
      if (!isValidTerm(search_term)) {
        return false;
      }

      const search_data = {
        "profile": "ces_profile_sgcareers",
        "suggestionQuery": "ces_suggestionQuery_sgcareers_suggestions_" + LANG,
        "text": search_term,
      };
      sendRequest(search_data, SUGGESTIONS_URL, callback);
    };

    const topSearch = function (callback, topSearchs) {
      if (callback instanceof Function) {
        callback(topSearchs);
      }
    };

    const oMod = {
      jobSearch: jobSearch,
      jobByIdSearch: jobByIdSearch,
      pageSearch: pageSearch,
      suggestionSearch: suggestionSearch,
      topSearch: topSearch,
    }

    return oMod;
  })();

  sgc_quantum.auth_api = (function () {
    // Cache du token avec expiration (55 minutes, le token expire à 60 min)
    let cachedToken = null;
    let tokenExpiration = null;
    const TOKEN_LIFETIME_MS = 55 * 60 * 1000;

    const getToken = function () {
      // Si le token est en cache et non expiré, le retourner
      if (cachedToken && tokenExpiration && Date.now() < tokenExpiration) {
        return Promise.resolve(cachedToken);
      }

      // Si une requête est déjà en cours, la réutiliser
      if (getTokenPromise) {
        return getTokenPromise;
      }

      getTokenPromise = fetch('/sg-careers-offers/get-token', {
        method: 'GET',
        headers: {
          'X-Requested-With': 'XMLHttpRequest',
          'X-CSRF-Token': drupalSettings.csrfToken
        },
        credentials: 'same-origin'
      })
      .then(response => {
        if (!response.ok) {
          throw new Error("Erreur lors de la récupération du token, HTTP " + response.status);
        }
        return response.json();
      })
      .then(json => {
        if (!json.token) {
          throw new Error("Réponse invalide, token absent");
        }
        // Mettre en cache le token
        cachedToken = json.token;
        tokenExpiration = Date.now() + TOKEN_LIFETIME_MS;
        return json.token;
      })
      .catch(err => {
        console.error("Erreur getToken :", err);
        throw err;
      })
      .finally(() => {
        getTokenPromise = null;
      });
      return getTokenPromise;
    };

    const oMod = {
      getToken: getToken,
    };

    return oMod;
  })();

  // Pré-charger le token au chargement de la page
  sgc_quantum.auth_api.getToken();
})(window, document, window.jQuery);
