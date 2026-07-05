/*
 * Fetch Opportunity - Result Set
 */

const OPPORTUNITY_AGGREGATE_BTN_SELECTOR = ".cmp-opportunity-aggregate .button--done";
var resultData = {
    totalResults: 0
};
$(document).ready(function () {
    const oppAggComp = $(".cmp-opportunity-aggregate");
    if(oppAggComp == undefined && oppAggComp.length <=0 ){
        return;
    }
    $(window).resize(function () {
        if (window.innerWidth > 1024) {
            $(".floatingMenu").hide();
            $(".floatingMenu").removeClass("topMenu");
            $(".cmp-opportunity--filter__accordion").show();
            if (resultData.totalResults !== 0) {
                $(".cmp-opportunity--result__set").show();
                $(".resultsFound").show();
                $(".resultsSort").show();
            } else {
                $(".cmp-opportunity--result__set").hide();
            }
        } else {
            if (resultData.resultSet) {
                $(".floatingMenu").show();
                if ($(".floatingMenu").hasClass("topMenu")) {
                    $(".cmp-opportunity--filter__accordion").show();
                } else {
                    $(".cmp-opportunity--filter__accordion").hide();
                    $(".resultsFound").show();
                    if (resultData.totalResults !== 0) {
                        $(".resultsFound").show();
                        $(".resultsSort").show();
                    } else {
                        $(".resultsSort").hide();
                    }
                }
            }
        }


    });

    $(".opportunity-aggregate .floatingMenu .action-button").on("click", function () {
        $(".cmp-opportunity--filter__accordion").toggle();
        $(".cmp-opportunity--result__set").toggle();
        // $(".resultsFound").toggle();
        // $(".resultsSort").toggle();
        $(".floatingMenu").toggleClass("topMenu");
        $(this).blur();
        window.scrollTo({
            top: 0,
            left: 0,
            behavior: 'smooth'
        });
    });

    $(OPPORTUNITY_AGGREGATE_BTN_SELECTOR).on('click', function () {
        $(".cmp-opportunity--filter__slick.slick-initialized").slick('slickUnfilter');
        $(".cmp-opportunity--filter__slick.slick-initialized").slick('unslick');
        $(".cmp-opportunity--filter--resultset").removeClass("slick--enabled");
        $(".cmp-opportunity--filter--resultset").prev().hide();
        $(".helpUs, .what--looking--title, .nextButton").hide();
        if (window.innerWidth <= 1024) {
            $(".floatingMenu").show();
            $(".cmp-opportunity--filter__accordion").hide();
        }
        $(".accordion--filterby__wrapper, .accordion--joblevel__wrapper, .filter-done").show();
        if (experienceHire) {
            $(".cmp-opportunity--filter--resultset.experienceHire").show();
            $(".cmp-opportunity--filter--resultset.studentsandgrads").hide();
            expHireResutSet();

        } else {
            $(".cmp-opportunity--filter--resultset.experienceHire").hide();
            $(".cmp-opportunity--filter--resultset.studentsandgrads").show();
            studGrandsResutSet();

        }
        $(REMOVE_BUTTON_SELECTOR).on("click", deleteHandler);
        //$(".cmp-opportunity--filter--resultset").css("display","grid");
        bindAccordioCheckBox();
        switchToProgram();

        var getQueryParams = getSelectedValues();
        createRequestResultSet(getQueryParams);
    });


});


var global_var;

function fetchResultSet(jsonPath) {

    const oppAggComp = $(".cmp-opportunity-aggregate");
    if(oppAggComp == undefined && oppAggComp.length <=0 ){
        return;
    }
    let jsonData;
    $.ajax({
        type: "GET",
        crossDomain: true,
        xhrFields: {
            withCredentials: true
        },
        url: jsonPath,
        async: false,
        contentType: 'text/plain',
        dataType: 'json',
        success: function (result) {
            jsonData = result;
        },
        failure: function (response) {
            console.log("failure: response: " + response);
        }
    });
    global_var = 1;
    return jsonData;
}

let currentPage = 1;
let options = {
    "records_per_page": 10
}

function selectFilterByOptionsByKeyword(keywordFilteredResultArr) {

    const businessAreaArr = [];
    const educationLevelArr = [];
    const programTypeArr = [];

    keywordFilteredResultArr.forEach(function (item) {

        if (item.businessArea) {
            if (businessAreaArr.indexOf(item.businessArea) == -1) {
                businessAreaArr.push(item.businessArea);
            }
        }
        if (item.educationLevel) {
            const splitArr = item.educationLevel.split(",");
            for (i = 0; i < splitArr.length; i++) {
                if (educationLevelArr.indexOf(splitArr[i]) == -1) {
                    educationLevelArr.push(splitArr[i]);
                }
            }

        }
        if (item.employmentType) {
            if (programTypeArr.indexOf(item.employmentType) == -1) {
                programTypeArr.push(item.employmentType);
            }
        }
    });

    let businessInputStart = experienceHire ? '.experienceHire .accordion--businessarea__filters input[name="' : '.studentsandgrads .accordion--businessarea__filters input[value="';
    let businessInputEnd = '"]';

    $.each(businessAreaArr, function (i, val) {
        if (experienceHire) {
            val = val.trim().toLowerCase().replace(" ", "-");
            $(businessInputStart + val + businessInputEnd).click();
        } else {
            if ($(businessInputStart + val + businessInputEnd).length > 1) {
                $(businessInputStart + val + businessInputEnd)[0].click();
            } else {
                $(businessInputStart + val + businessInputEnd).click();
            }
        }
    });

    let educationInputStart = '.accordion--educationlevel__filters input[value="';
    let educationInputEnd = '"]';

    $.each(educationLevelArr, function (i, val) {
        $(educationInputStart + val + educationInputEnd).click();
    });

    let programInputStart = experienceHire ? '.experienceHire .accordion--programtype__filters input[value="' : '.studentsandgrads .accordion--programtype__filters input[value="';
    let programInputEnd = '"]';

    $.each(programTypeArr, function (i, val) {
        $(programInputStart + val + programInputEnd).click();
    });

}

var pagefunc = Pagination('opportunityPagination');

function generateResult(resultSet, currentPage, keyword, noautoselect) {

    $(".cmp-opportunity--result__set, .noreuslt--jobcard").empty();

    resultData = resultSet;
    let results = resultSet.resultSet;

    if (global_var == 1) {
        let results = resultSet.resultSet.reverse();
        global_var = 2;
        if ($('.sort-down:visible').length > 0) {
            $('.sort-down').css({'transform': 'rotate(223deg)'});
            $('.sort-down').css({'margin-bottom': '0px'});
            global_var = 3;
        }
    } else if (global_var == 3) {
        if ($('.sort-down:visible').length > 0) {
            $('.sort-down').css({'transform': 'rotate(223deg)'});
        } else {
            $('.sort-up').css({'transform': 'rotate(45deg)'});
        }
    }

    totalResultsFound = resultSet.resultSet.length;
    let showresultSet = paginationOpportunity(results, currentPage, options);
    for (let i = 0; i < showresultSet.length; i++) {
        let data = showresultSet[i];
        if (data != undefined) {
            if (resultSet.totalResults > 0) {
                $(".cmp-opportunity--result__set").append(getResultsDiv(data));
                $(".cmp-opportunity--result__set").show();
                $(".resultsSort").show();
                $(".no-results-found ").hide();
                newWindowLinks();
            } else {
                $(".no-results-found ").show();
                $(".resultsSort").hide();
                $(".cmp-opportunity--result__set").hide();
                $(".no-results-found .noreuslt--jobcard").append(getNoResultsDiv(i, showresultSet));
            }
            $(".resultsFound").text(resultSet.totalResults + " Results Found");
            if (keyword) {
                $(".resultsFound").text(resultSet.totalResults + " Results Found For ");
                $(".resultsFound").append('<span>"' + keyword + '"</span>');
            }

        }
    }

    if (experienceHire) {
        $(".experienceHire .cmp-opportunity--result__set").append("<div class='opportunity pagination' id='pagination'></div>");
    } else {
        $(".studentsandgrads .cmp-opportunity--result__set").append("<div class='opportunity pagination' id='pagination'></div>");
    }

    let pages = Math.ceil(resultSet.resultSet.length / options.records_per_page);

    pagefunc.Init(document.getElementById('pagination'), {
        size: pages, // pages size
        page: currentPage,  // selected page
        step: 1,   // pages before and after current
        results: resultSet, // data to show
        changedata: generateResult, // call back function for data change
        class: 'opportunity'
    });


    if (keyword && resultSet.totalResults > 0 && noautoselect !== "noautoselect") {
        setTimeout(function () {
            selectFilterByOptionsByKeyword(resultSet.resultSet);
        }, 1500);
    }

    $(".cmp-opportunity--result__set .cmp-jobcard__link").on("click", function () {
        if (window.innerWidth < 767) {
            if (experienceHire) {
                let url = $(this).find(".button--done").attr("href");
                window.open(url);
            } else {
                let url = $(this).find(".learn-more").attr("href");
                window.open(url);
            }

        }
    });

    $(".cmp-opportunity-aggregate .jobcard_arrow").click(function () {
        $(this).blur();
        let parentDiv = this.parentElement,
            analyticsVal = this.getAttribute("data-analytics-link");

        if ($(this).hasClass('down')) {
            $(this).removeClass('down').addClass('up');
            if (analyticsVal) $(this).attr("data-analytics-link", analyticsVal.replace("Expand", "Collapse"));

        } else {
            $(this).removeClass('up').addClass('down');
            if (analyticsVal) $(this).attr("data-analytics-link", analyticsVal.replace("Collapse", "Expand"));
        }

        $(parentDiv).find('.description_section').toggle();
    });

    $(".cmp-opportunity-aggregate .jobcard_arrow").on('keydown', function (event) {
        var keyCode = event.keyCode || event.which;
        event.preventDefault();
        if (keyCode == 13 || event.keyCode == 27) {
            $(this).click().blur();
            $(".btn.learn-more").focus();
        }
        if (keyCode == 9) {
            $(this).blur();
        }

    });


    let scrollUp = 320;
    if (window.innerWidth < 767) {
        scrollUp = 220;
    } else if (window.innerWidth < 1025) {
        scrollUp = 250;
    }
    window.scrollTo({
        top: scrollUp,
        left: 0,
        behavior: 'smooth'
    });

    if (window.innerWidth <= 1024) {
        $(".floatingMenu").show();
        $(".cmp-opportunity--filter__accordion").hide();
    }
}

/**
 * Truncate the string on given number of character of the first line break.
 * @param { string to truncate} str
 * @param { number of character} count
 */
function ellipsify(str, count) {
    if (str) {
        // let patt1 = /\n/;
        // let index = str.search(patt1);
        // if(index !== -1 && index <= count) {
        //     count = index - 1;
        // }
        if (str.length > count) {
            return (str.substring(0, count) + "...");
        } else {
            return str;
        }
    }
}


function getResultsDiv(data) {
    let empType = data.opportunity == "EXPERIENCED PROFESSIONALS" ? "Job" : "Program";
    var dataLocation = data.location == null ? "" : data.location.replace("Korea, Republic of", "Republic of Korea");
    var typeOfEvent = experienceHire ? "Employment Type" : "Program Type";
    var appPostDate = experienceHire ? "Posted Date" : "Application Deadline";
    let flowType = experienceHire ? "EP" : "S&G";
    let descripton = data.jobDescription;

    let jobsType = experienceHire ? "ep." : "sg.";

    var url = window.location.href;
    var arr = url.split("/");
    var contextPath = '/' + arr[3];
    var origin = window.location.origin;
    var careerUrl = '';
    if (experienceHire) {
        careerUrl = data.url;
    } else {
        if (arr[3] === 'auth' || arr[3] === 'pub' || arr[3] === 'content') {
            careerUrl = origin + contextPath + '/msdotcom/en/careers/students-graduates/opportunities.' + data.jobNumber + '.html?wcmmode=disabled';
        } else {
            careerUrl = origin + '/careers/students-graduates/opportunities/' + data.jobNumber;
        }
    }
    if (navigator.userAgent.search("MSBrowserIE") !== -1) {
        descripton = ellipsify(data.jobDescription, 300)
    }

    if (window.innerWidth < 767) {

        let childDiv = "<div class='jobcard'>" +
            "<div class='cmp-jobcard'>" +
            '<div class="cmp-jobcard__link" ' +
            'data-analytics-link="' + capitalizeString(data.opportunity) + ' | ' + data.jobTitle + ' | Apply Now"' +
            'data-analytics-module="Opportunity Card | ' + data.jobTitle + ' | NA"' +
            'data-analytics-job-card="' + flowType + ' | ' + data.jobTitle + ' | ' + dataLocation + ' | ' + data.businessArea + ' | ' + data.employmentType + ' | ' + data.jobNumber + '"' +
            'data-analytics-button-cta="' + capitalizeString(data.opportunity) + ' | ' + data.jobTitle + ' | Apply Now"' +
            'data-analytics-job-url="' + data.url + ' ">' +
            '<div class="cmp-jobcard__content">' +
            '<div class="eyebrow_title_section"><div class="cmp-jobcard__eyebrow">' + data.opportunity + '</div>' +
            '<div class="cmp-jobcard__title">' + data.jobTitle + '</div>' +
            '<div class="cmp-jobcard__separator purple"></div></div>' +
            '<div class="description_section " style="display: none" >' +
            '<div>' + appPostDate + ': ' + data.applicationDate + '</div>' +
            '<div>' + typeOfEvent + ': ' + data.employmentType + '</div>' +
            '<div class="apply_button"><a class="button--done" ' +
            'href="' + data.url + '" target="_blank">Apply Now</a></div>' +
            // '<h4>'+empType+' Description </h4>' +
            // '<div class="description_text">' + data.jobDescription + '</div>' +
            '<div class="CTA-button"><a class="btn learn-more fw-700"' +
            'data-analytics-link="' + capitalizeString(data.opportunity) + ' | ' + data.jobTitle + ' | Learn More"' +
            'data-analytics-module="Opportunity Card | ' + data.jobTitle + ' | NA"' +
            'data-analytics-job-card="' + flowType + ' | ' + data.jobTitle + ' | ' + dataLocation + ' | ' + data.businessArea + ' | ' + data.employmentType + ' | ' + data.jobNumber + '"' +
            'href="' + careerUrl + '" target="_blank">Learn More</a> </div></div>' +
            '<div class="role_city_section"><div class="cmp-jobcard__role">' + data.businessArea + '</div>' +
            '<div class="cmp-jobcard__location">' + dataLocation + '</div>' +
            '<div class="jobId"> Job # ' + data.jobNumber + '</div></div>' +
            '<div class="jobcard_arrow down" data-analytics-link="' + capitalizeString(data.opportunity) + ' | ' + data.jobTitle + ' | Expand"></div>' +
            '</div>' +
            '</div>' +
            '</div>' +
            '</div>';
        return childDiv;

    } else {
        let childDiv = "<div class='jobcard'>" +
            "<div class='cmp-jobcard'>" +
            '<div class="cmp-jobcard__link" >' +
            '<div class="cmp-jobcard__content">' +
            '<div class="eyebrow_title_section"><div class="cmp-jobcard__eyebrow">' + data.opportunity + '</div>' +
            '<div class="cmp-jobcard__title">' + data.jobTitle + '</div>' +
            '<div class="cmp-jobcard__separator purple"></div></div>' +

            '<div class="role_city_section"><div class="cmp-jobcard__role">' + data.businessArea + '</div>' +
            '<div class="cmp-jobcard__location">' + dataLocation + '</div>' +
            '<div class="jobId"> Job # ' + data.jobNumber + '</div></div>' +
            '<div class="apply_button"><a class="button--done" ' +
            'data-analytics-link="' + capitalizeString(data.opportunity) + ' | ' + data.jobTitle + ' | Apply Now"' +
            'data-analytics-module="Opportunity Card | ' + data.jobTitle + ' | NA"' +
            'data-analytics-job-card="' + flowType + ' | ' + data.jobTitle + ' | ' + dataLocation + ' | ' + data.businessArea + ' | ' + data.employmentType + ' | ' + data.jobNumber + '"' +
            'data-analytics-button-cta="' + capitalizeString(data.opportunity) + ' | ' + data.jobTitle + ' | Apply Now"' +
            'href="' + data.url + '" target="_blank">Apply Now</a></div>' +
            '<div class="jobcard_arrow down"  tabindex="0" data-analytics-link="' + capitalizeString(data.opportunity) + ' | ' + data.jobTitle + ' | Expand"></div>' +
            '<div class="application-date">' + appPostDate + ': ' + data.applicationDate + '</div>' +
            '<div class="typeof-event">' + typeOfEvent + ': ' + data.employmentType + '</div>' +
            '<div class="description_section " style="display: none" >' +
            '<h4>' + empType + ' Description </h4>' +
            '<div class="description_text">' + descripton + '</div>' +
            '<div class="CTA-button"><a class="btn learn-more fw-700" tabindex="0" ' +
            'data-analytics-link="' + capitalizeString(data.opportunity) + ' | ' + data.jobTitle + ' | Learn More"' +
            'data-analytics-module="Opportunity Card | ' + data.jobTitle + ' | NA"' +
            'data-analytics-job-card="' + flowType + ' | ' + data.jobTitle + ' | ' + dataLocation + ' | ' + data.businessArea + ' | ' + data.employmentType + ' | ' + data.jobNumber + '"' +
            'href="' + careerUrl + '" target="_blank">Learn More</a> </div></div>' +
            '</div>' +
            '</div>' +
            '</div>' +
            '</div>';
        return childDiv;
    }
}

function getNoResultsDiv(index, showResultSet) {
    let data = showResultSet[index], count = 0;
    var getLangSelection;
    for (let i = 0; i < showResultSet.length; i++) {
        if (showResultSet[i]) count++;
    }
    if (experienceHire) {
        getLangSelection = $($(".experienceHire .accordion--jobslevel__filters li").find("input:checked")).val();
    } else {
        getLangSelection = $($(".studentsandgrads .accordion--jobslevel__filters li").find("input:checked")).val();
    }
    if (getLangSelection === "FR") {
        $(".noresults-description").text("Il n’y a présentement aucune opportunité qui réponde à ce critère. Veuillez choisir de nouveaux filtres afin d’obtenir de nouveaux résultats");
    } else {
        $(".noresults-description").text("There are currently no opportunities open. Please choose other filters to see different results.");
    }
    var dataLocation = data.location == null ? "" : data.location.replace("Korea, Republic of", "Republic of Korea");
    var typeOfEvent = experienceHire ? "Employment Type" : "Program Type";
    var appPostDate = experienceHire ? "Posted Date" : "Application Deadline";
    let flowType = experienceHire ? "EP" : "S&G",
        childDiv = "<div class='jobcard'>" +
            "<div class='cmp-jobcard'>" +
            '<a class="cmp-jobcard__link" href="' + data.url + '" target="_blank"' +
            'data-analytics-link="' + capitalizeString(data.opportunity) + ' | ' + data.jobTitle + ' | NA"' +
            'data-analytics-module="Compact Opportunity Card | ' + data.jobTitle + ' | position ' + (index + 1) + ' of ' + count + '"' +
            'data-analytics-job-card="' + flowType + ' | ' + data.jobTitle + ' | ' + dataLocation + ' | ' + data.businessArea + ' | NA | NA">' +
            '<div class="cmp-jobcard__content">' +
            '<div class="eyebrow_title_section"><div class="cmp-jobcard__eyebrow">' + data.opportunity + '</div>' +
            '<div class="cmp-jobcard__title">' + data.jobTitle + '</div>' +
            '<div class="cmp-jobcard__separator purple"></div></div>' +
            '<div class="description_section " style="display: none" >' +
            '<div>' + appPostDate + ': ' + data.applicationDate + '</div>' +
            '<div>' + typeOfEvent + ': ' + data.employmentType + '</div>' +
            //  '<h4>Job Description </h4>' +
            //  '<div class="description_text">' + data.jobDescription + '</div>' +
            '<div class="CTA-button"><span class="btn learn-more fw-700">Learn More</span> </div></div>' +
            '<div class="role_city_section"><div class="cmp-jobcard__role">' + data.businessArea + '</div>' +
            '<div class="cmp-jobcard__location">' + dataLocation + '</div>' +
            '<div class="jobId"> Job # ' + data.jobNumber + '</div></div>' +
            '<div class="apply_button"><span class="button--done">Apply Now</span></div>' +
            '<div class="jobcard_arrow down"></div>' +
            '</div>' +
            '</a>' +
            '</div>' +
            '</div>';
    return childDiv;
}

/**
 * Finds out the ancestor of the current element based on the class name
 * @param {*Current element} el
 * @param {*Ancestor className} cls
 */
function findAncestor(el, cls) {
    while ((el = el.parentElement) && !el.classList.contains(cls)) ;
    return el;
}

/**
 * paginationating the data
 * @param {*Which page data to show} currentPage
 * @param {* other parameters} options
 */
function paginationOpportunity(data, currentPage, options) {
    let newArray = [];
    for (let i = (currentPage - 1) * options.records_per_page; i < (currentPage * options.records_per_page); i++) {
        newArray.push(data[i]);
    }
    return newArray;
}



var LOCATION_FILTER_PATH = "/content/dam/msdotcom/appdata/",
    OPPORTUNITY_AGGREGATE_CLASS = "cmp-opportunity-aggregate",
    EVENTS_AGGREGATE_CLASS = "cmp-events-aggregate",
    LOCATION_FILTER_CLASS = "accordion--location__filters",
    RESULTSET_CLASS = "cmp-opportunity--filter--resultset",
    EP_UL_CLASS_SELECTOR = ".experienceHire .location-selection-section",
    SG_UL_CLASS_SELECTOR = ".studentsandgrads .location-selection-section",
    EVENTS_UL_CLASS_SELECTOR = '.events .location-selection-section',
    EP_BUTTON_SELECTOR = ".job-experience a",
    SG_BUTTON_SELECTOR = ".intern-students a",
    PARENT_CLEAR_SELECTION_CLASS = "clearSelection",
    LOCATION_LABEL_CLASS = "accordion--location__label",
    CLEAR_ALL_BUTTON_CLASS = "accordion--filterby__clear",
    FILTER_DONE_BTN_SELECTOR = ".filter-done a",
    idSelector, locationSelectionSec, regions, parentEl, regionData, regionSelection, clearSelectionBtn, addButton;
var addLocationBoolean = false;


$(document).ready(function () {
    const oppAggComp = $(".cmp-opportunity-aggregate");
    if(oppAggComp == undefined && oppAggComp.length <=0 ){
        return;
    }
    $(EP_BUTTON_SELECTOR).on("click", function () {
        idSelector = "ep";
        regionData = false;
        LOCATION_FILTER_PATH = "/content/dam/msdotcom/appdata/";
        if (epBackButtonFlag) {
            //initLocationSet();
            loadEPLocation();
        }
    });

    $(SG_BUTTON_SELECTOR).on("click", function () {
        idSelector = "sg";
        regionData = false;
        LOCATION_FILTER_PATH = "/content/dam/msdotcom/appdata/";
        if (sgBackButtonFlag) {
            initLocationSet();
        }
    });


    let url_string = location.href;
    // let  url = new URL(url_string);
    // let selector = url.searchParams.get("opportunity");
    // if(selector) {
    //     idSelector = selector;
    //     initLocationSet();
    // }

    let findep = url_string.search('=ep');
    let findsg = url_string.search('=sg');
    if (findep > -1) {
        idSelector = "ep";
        initLocationSet();
    } else if (findsg > -1) {
        idSelector = "sg";
        initLocationSet();
    } else {
        idSelector = "events";
        initLocationSet();
    }
    let intialWindowWidth = window.innerWidth;
    $(window).resize(function () {
        if (window.innerWidth >= 768 && intialWindowWidth < 768) {
            locationFilterAlignment(0, idSelector, true)
        }
        if (intialWindowWidth >= 768 && window.innerWidth < 768) {
            locationFilterAlignment(0, idSelector, true)
        }
        intialWindowWidth = window.innerWidth;
    });

    $(".button--jobExperience, .button--internStudents").on('keydown', function (event) {
        var keyCode = event.keyCode || event.which;
        if (keyCode == 13 || event.keyCode == 27) {
            $('.backButton').focus();
        }
    });

    $(".backButton").on('keydown', function (event) {
        var keyCode = event.keyCode || event.which;

        if (keyCode == 9 && (!event.shiftKey)) {
            event.preventDefault();
            $(this).blur();
            $(".slick-dots .slick-active").focus();
            $(".region-list .region-item").attr("tabindex", "-1");
            $(".region-list .region-item.active").focus().attr("tabindex", "0");
        }
        if (keyCode == 13 || event.keyCode == 27) {
            $(this).click();
        }
    });

});

/**
 * for mobile version location fliter reodering
 */

function locationFilterAlignment(type, idSelector) {
    if (window.innerWidth < 767) {
        $("#" + idSelector + "-clear-selection").appendTo(".region-item.active");
        if (type !== 1) {
            $("#" + idSelector + "-region-0").appendTo("#region-item-0");
            $("#" + idSelector + "-region-1").appendTo("#region-item-1");
            $("#" + idSelector + "-region-2").appendTo("#region-item-2");
            $("#" + idSelector + "-region-3").appendTo("#region-item-3");
        }
        $("#" + idSelector + "-add-location").appendTo(".region-item.active");
    } else {
        $("#" + idSelector + "-regions").before($("#" + idSelector + "-clear-selection"));
        $("#" + idSelector + "-regions").after($("#" + idSelector + "-add-location"));
        $("#" + idSelector + "-regions").after($("#" + idSelector + "-region-0"));
        $("#" + idSelector + "-regions").after($("#" + idSelector + "-region-1"));
        $("#" + idSelector + "-regions").after($("#" + idSelector + "-region-2"));
        $("#" + idSelector + "-regions").after($("#" + idSelector + "-region-3"));

        let activeId = $(".region-list .region-item.active").attr('id');
        if (activeId) {
            let indexs = activeId.split("-");
            let index = indexs[indexs.length - 1];
            $("#" + idSelector + "-region-" + index).show();
            $("#region-item-" + index).addClass("active");
        } else {
            $("#" + idSelector + "-clear-selection").show();
            $("#" + idSelector + "-region-0").show();
            $("#region-item-0").addClass("active");
            $("#" + idSelector + "-add-location").show();
        }
    }
}

/*accessability code*/
function accessibilityLocation() {
    $(".region-item").keydown(function (e) {
        if (e.keyCode == 40) {
            var selected = $('.region-item:focus').index();
            var totalLength = $(' .region-item').length;

            if (selected === totalLength - 1) {
                return
            }
            var next = selected + 1;
            var pre = selected - 1;
            if (pre < 0)
                pre = 0;
            if (next > $(' .region-item').length)
                next = $(' .region-item').length;


            let that = $(' .region-item').eq(next).focus();
            let regionName = $(that).attr("value"),
                filterParent = findAncestor(this, LOCATION_FILTER_CLASS),
                targetRegion = filterParent.querySelector("[data-region-name='" + regionName + "']");
            if (targetRegion && getActualLocationSelections(targetRegion).length > 0) {

                enableClearSelections(targetRegion);
            } else clearSelectionBtn.classList.add("disabled");
            let regionIndex = $(regions).index(that);
            if (window.innerWidth < 767)
                if ($(that).hasClass("active")) {
                    $(that).removeClass("active");
                    $(that).attr('tabindex', -1);
                    regionSelection = that.getAttribute("value");
                    //idSelector === "ep" ? hideAllSelections(EP_UL_CLASS_SELECTOR) : hideAllSelections(SG_UL_CLASS_SELECTOR);
                    if (idSelector === "ep") {
                        hideAllSelections(EP_UL_CLASS_SELECTOR)
                    } else if (idSelector === "sg") {
                        hideAllSelections(SG_UL_CLASS_SELECTOR);
                    } else {
                        hideAllSelections(EVENTS_UL_CLASS_SELECTOR);
                    }
                    let targetRegionDdSec = document.getElementById(idSelector + "-region-" + regionIndex);
                    let clearButton = document.getElementById(idSelector + "-clear-selection");
                    let addButton = document.getElementById(idSelector + "-add-location");
                    if (targetRegionDdSec) {
                        targetRegionDdSec.style.display = "none";
                        // locationFilterAlignment(1, idSelector);
                    }
                    if (clearButton && addButton) {
                        addButton.style.display = "none";
                        clearButton.style.display = "none";
                    }
                    return;
                }
            $("#" + idSelector + "-regions .region-item").removeClass("active");
            $("#" + idSelector + "-regions .region-item").attr('tabindex', -1);
            $(that).addClass("active");
            $(that).attr('tabindex', 0);
            //  regionSelection = that.getAttribute("value");
            regionSelection = $(that).attr("value");
            //idSelector === "ep" ? hideAllSelections(EP_UL_CLASS_SELECTOR) : hideAllSelections(SG_UL_CLASS_SELECTOR);
            if (idSelector === "ep") {
                hideAllSelections(EP_UL_CLASS_SELECTOR)
            } else if (idSelector === "sg") {
                hideAllSelections(SG_UL_CLASS_SELECTOR);
            } else {
                hideAllSelections(EVENTS_UL_CLASS_SELECTOR);
            }
            let targetRegionDdSec = document.getElementById(idSelector + "-region-" + regionIndex);
            let clearButton = document.getElementById(idSelector + "-clear-selection");
            let addButton = document.getElementById(idSelector + "-add-location");
            if (clearButton && addButton) {
                addButton.style.display = "block";
                clearButton.style.display = "block";
            }
            if (targetRegionDdSec) {
                targetRegionDdSec.style.display = "block";
                locationFilterAlignment(1, idSelector);

            } else createNewRegionSelctionSec(regionIndex, regionSelection);

        }


        if (e.keyCode == 38) {
            var selected = $(' .region-item:focus').index();

            var next = selected + 1;
            var pre = selected - 1;
            if (pre < 0)
                pre = 0;
            if (next > $(' .region-item').length)
                next = $(' .region-item').length;


            let that = $('  .region-item').eq(pre).focus();
            let regionName = $(that).attr("value"),
                filterParent = findAncestor(this, LOCATION_FILTER_CLASS),
                targetRegion = filterParent.querySelector("[data-region-name='" + regionName + "']");
            if (targetRegion && getActualLocationSelections(targetRegion).length > 0) {
                enableClearSelections(targetRegion);
            } else clearSelectionBtn.classList.add("disabled");
            let regionIndex = $(regions).index(that);
            if (window.innerWidth < 767)
                if ($(that).hasClass("active")) {
                    $(that).removeClass("active");
                    $(that).attr('tabindex', -1);
                    regionSelection = that.getAttribute("value");
                    //idSelector === "ep" ? hideAllSelections(EP_UL_CLASS_SELECTOR) : hideAllSelections(SG_UL_CLASS_SELECTOR);
                    if (idSelector === "ep") {
                        hideAllSelections(EP_UL_CLASS_SELECTOR)
                    } else if (idSelector === "sg") {
                        hideAllSelections(SG_UL_CLASS_SELECTOR);
                    } else {
                        hideAllSelections(EVENTS_UL_CLASS_SELECTOR);
                    }
                    let targetRegionDdSec = document.getElementById(idSelector + "-region-" + regionIndex);
                    let clearButton = document.getElementById(idSelector + "-clear-selection");
                    let addButton = document.getElementById(idSelector + "-add-location");
                    if (targetRegionDdSec) {
                        targetRegionDdSec.style.display = "none";
                        // locationFilterAlignment(1, idSelector);
                    }
                    if (clearButton && addButton) {
                        addButton.style.display = "none";
                        clearButton.style.display = "none";
                    }
                    return;
                }
            $("#" + idSelector + "-regions .region-item").removeClass("active");
            $("#" + idSelector + "-regions .region-item").attr('tabindex', -1);
            $(that).addClass("active");
            $(that).attr('tabindex', 0);
            //  regionSelection = that.getAttribute("value");
            regionSelection = $(that).attr("value");
            //idSelector === "ep" ? hideAllSelections(EP_UL_CLASS_SELECTOR) : hideAllSelections(SG_UL_CLASS_SELECTOR);
            if (idSelector === "ep") {
                hideAllSelections(EP_UL_CLASS_SELECTOR)
            } else if (idSelector === "sg") {
                hideAllSelections(SG_UL_CLASS_SELECTOR);
            } else {
                hideAllSelections(EVENTS_UL_CLASS_SELECTOR);
            }
            let targetRegionDdSec = document.getElementById(idSelector + "-region-" + regionIndex);
            let clearButton = document.getElementById(idSelector + "-clear-selection");
            let addButton = document.getElementById(idSelector + "-add-location");
            if (clearButton && addButton) {
                addButton.style.display = "block";
                clearButton.style.display = "block";
            }
            if (targetRegionDdSec) {
                targetRegionDdSec.style.display = "block";
                locationFilterAlignment(1, idSelector);

            } else createNewRegionSelctionSec(regionIndex, regionSelection);

        }

    });
}

function loadEPLocation() {
    LOCATION_FILTER_PATH += 'filter-metadata-location.json';
    // LOCATION_FILTER_PATH += 'filter-metadata-'+idSelector+'.json';
    locationData = fetchDropdownJson(LOCATION_FILTER_PATH);

    var epLocationDropDown = $(".ep-dropdown-location");
    if (!addLocationBoolean) {
        $.each(locationData, function (key, value) {
            epLocationDropDown.append($('<option>', {
                value: value.name,
                text: value.text,
                'data-url': value.url,
                'data-linkopen': value.open
            }));
        });
    }
    addLocationBoolean = true;
}

function updateResults() {
    var selectLocation = $(".ep-dropdown-location");
    var selectGoBtn = $(".button--go");
    var getSelIndex = selectLocation[0].options.selectedIndex;
    var getSelLocation = selectLocation[0].options[getSelIndex].innerText;
    if (getSelIndex == 0) {
        selectGoBtn.off();
        selectGoBtn.addClass("disabled");
        selectGoBtn.attr("href", "javascript:void(0);")
        selectGoBtn.removeAttr("target");
    } else {
        selectGoBtn.removeClass("disabled");
        selectGoBtn.attr("data-analytics-link", "In-Line Career Finder | Select Location | " + getSelLocation + " | Go");
        selectLocation.attr("data-analytics-dropdown", "In-Line Career Finder | Dropdown | Select Location | " + getSelLocation);
        var getLinkOpen = selectLocation[0].options[getSelIndex].dataset.linkopen;
        if (getLinkOpen === "external") {
            selectGoBtn.off("click");
            selectGoBtn.removeAttr("results-parameter");
            selectGoBtn.attr("href", selectLocation[0].options[getSelIndex].dataset.url);
            selectGoBtn.attr('target', '_blank');
            newWindowLinks();
        } else {
            selectGoBtn.removeAttr("href");
            selectGoBtn.attr("results-parameter", selectLocation[0].options[getSelIndex].dataset.url);
            selectGoBtn.attr('target', '_self');
            $(".button--go .new-window-icon, .button--go .new-window-icon .screen-reader-only").hide();
            selectGoBtn.click(function (e) {
                e.preventDefault();
                onClickFetchResult();
                $(".cmp-opportunity--regionSelectors").hide();
            });
        }
    }
}

/**
 *  Initialization
 */
function initLocationSet() {

    let regionSel = document.getElementById(idSelector + "-regions"),
        countrySel = document.getElementById(idSelector + "-region-0-country-0"),
        stateSel = document.getElementById(idSelector + "-region-0-state-0"),
        citySel = document.getElementById(idSelector + "-region-0-city-0");

    if (regionSel) {
        regionSel.innerHTML = "";
    }
    if (idSelector === 'events') {
        parentEl = document.getElementsByClassName(EVENTS_AGGREGATE_CLASS);
    } else {
        parentEl = document.getElementsByClassName(OPPORTUNITY_AGGREGATE_CLASS);
    }
    if (!parentEl.length) return;
    // let contextPath = parentEl[0].getAttribute("data-context-path");
    LOCATION_FILTER_PATH += 'filter-metadata-' + idSelector + '.json';
    if (!regionData) regionData = fetchDropdownJson(
        LOCATION_FILTER_PATH)
    if (!regionData) return;

    /* Populate all the regions */
    let ulEl = document.createElement("ul");
    ulEl.setAttribute("class", "region-list");
    let index = 0;
    for (let key in regionData) {
        if (key === "text" || key === "title") continue;
        let listItem = document.createElement("li");
        listItem.setAttribute("class", "region-item");
        listItem.setAttribute("value", key);
        listItem.setAttribute("id", "region-item-" + index);
        listItem.setAttribute("tabindex", -1);
        let Itemspan = document.createElement("span");
        Itemspan.setAttribute("class", "region-item-text");
        Itemspan.setAttribute("value", key);
        Itemspan.appendChild(document.createTextNode(regionData[key].text))
        listItem.appendChild(Itemspan);
        ulEl.appendChild(listItem);
        index++;

    }
    regionSel.appendChild(ulEl);
    /**/


    regions = regionSel.querySelectorAll(".region-item");
    regionsSpan = regionSel.querySelectorAll(".region-item-text");
    locationSelectionSec = document.querySelector("#" + idSelector + "-region-0");
    if (!regions.length) return;
    regionSelection = regions[0].getAttribute("value");
    locationSelectionSec.setAttribute("data-region-name", regionSelection);
    $(locationSelectionSec).find('select[name=country]').attr('aria-label', 'Region ' + regionSelection);
    $(locationSelectionSec).find('select[name=state]').attr('aria-label', 'All states');
    $(locationSelectionSec).find('select[name=city]').attr('aria-label', 'All cities');
    /* Populate countries with first region */
    if (window.innerWidth > 767)
        $(regions[0]).addClass("active");
    $(regions[0]).attr('tabindex', 0);
    populateCountries(countrySel, stateSel, citySel, regionSelection);


    clearSelectionBtn = document.getElementById(idSelector + "-clear-selection");
    addButton = document.getElementById(idSelector + "-add-location");
    if (window.innerWidth < 767) {
        clearSelectionBtn.style.display = "none";
        addButton.style.display = "none";
        hideAllSelections(EP_UL_CLASS_SELECTOR);
        hideAllSelections(SG_UL_CLASS_SELECTOR);
        hideAllSelections(EVENTS_UL_CLASS_SELECTOR);
    }

    accessibilityLocation();


    /* Populate countries on click on other region */
    for (let count = 0; count < regionsSpan.length; count++) {
        regionsSpan[count].onclick = function () {
            let that = regions[count];
            let regionName = $(this).attr("value"),
                filterParent = findAncestor(this, LOCATION_FILTER_CLASS),
                targetRegion = filterParent.querySelector("[data-region-name='" + regionName + "']");
            if (targetRegion && getActualLocationSelections(targetRegion).length > 0) {
                enableClearSelections(targetRegion);
            } else clearSelectionBtn.classList.add("disabled");
            let regionIndex = $(regions).index($(this).parent());
            if (window.innerWidth < 767)
                if ($(this).parent().hasClass("active")) {
                    $(this).parent().removeClass("active");
                    regionSelection = that.getAttribute("value");
                    //idSelector === "ep" ? hideAllSelections(EP_UL_CLASS_SELECTOR) : hideAllSelections(SG_UL_CLASS_SELECTOR);
                    if (idSelector === "ep") {
                        hideAllSelections(EP_UL_CLASS_SELECTOR)
                    } else if (idSelector === "sg") {
                        hideAllSelections(SG_UL_CLASS_SELECTOR);
                    } else {
                        hideAllSelections(EVENTS_UL_CLASS_SELECTOR);
                    }
                    let targetRegionDdSec = document.getElementById(idSelector + "-region-" + regionIndex);
                    let clearButton = document.getElementById(idSelector + "-clear-selection");
                    let addButton = document.getElementById(idSelector + "-add-location");
                    if (targetRegionDdSec) {
                        targetRegionDdSec.style.display = "none";
                        // locationFilterAlignment(1, idSelector);
                    }
                    if (clearButton && addButton) {
                        addButton.style.display = "none";
                        clearButton.style.display = "none";
                    }
                    return;
                }
            $("#" + idSelector + "-regions .region-item").removeClass("active");
            $(this).parent().addClass("active");
            regionSelection = this.getAttribute("value");
            //idSelector === "ep" ? hideAllSelections(EP_UL_CLASS_SELECTOR) : hideAllSelections(SG_UL_CLASS_SELECTOR);
            if (idSelector === "ep") {
                hideAllSelections(EP_UL_CLASS_SELECTOR)
            } else if (idSelector === "sg") {
                hideAllSelections(SG_UL_CLASS_SELECTOR);
            } else {
                hideAllSelections(EVENTS_UL_CLASS_SELECTOR);
            }
            let targetRegionDdSec = document.getElementById(idSelector + "-region-" + regionIndex);
            let clearButton = document.getElementById(idSelector + "-clear-selection");
            let addButton = document.getElementById(idSelector + "-add-location");
            if (clearButton && addButton) {
                addButton.style.display = "block";
                clearButton.style.display = "block";
            }
            if (targetRegionDdSec) {
                targetRegionDdSec.style.display = "block";
                locationFilterAlignment(1, idSelector);

            } else createNewRegionSelctionSec(regionIndex, regionSelection);

        };
    }


    /* On change on country populate states */
    registerCountryChange(countrySel, stateSel, citySel, regionSelection);

    /* On change on state populate cities */
    registerStateChange(countrySel, stateSel, citySel, regionSelection);

    /* On change on city, set few analytics attributes */
    registerCityChange(citySel);

    /* Reset Dropdowns */
    resetDropdows(stateSel, citySel);

    addButton.onclick = function () {
        addButtonHandler();
        $(this).children().blur();
    };

    clearSelectionBtn.onclick = function () {
        clearSelectionHandler(this);
    }

    ///accessibility

    $(".clearSelection").keydown(function (e) {
        if (e.keyCode == 13) {
            clearSelectionHandler(this);

        }
    });


    locationFilterAlignment(0, idSelector);
    // addLocationSelectForLi(".region-item",document.getElementsByClassName("location-selection-section"));
}

function addLocationSelectForLi(parentDiv, appendDiv) {
    var windowWidth = $(window).width();
    if (windowWidth < 768) {
        $(parentDiv).append(appendDiv);
    }
}

/**
 *  Add Button Handler
 */
function addButtonHandler() {
    let currentRegion = document.querySelector("#" + idSelector + "-regions .region-item.active"),
        regionIndex = $(regions).index(currentRegion),
        regionDdSec = document.getElementById(idSelector + "-region-" + regionIndex),
        selectionItems = regionDdSec.querySelectorAll("li");
    regionSelection = currentRegion.getAttribute("value");
    let clonedItem = selectionItems[0].cloneNode(true), deleteBtn;
    deleteBtn = createDeleteButton();
    clonedItem.appendChild(deleteBtn);
    clonedItem.querySelector("#" + idSelector + "-region-" + regionIndex + "-country-0").setAttribute("id", idSelector + "-region-" + regionIndex + "-country-" + (selectionItems.length)),
        clonedItem.querySelector("#" + idSelector + "-region-" + regionIndex + "-state-0").setAttribute("id", idSelector + "-region-" + regionIndex + "-state-" + (selectionItems.length)),
        clonedItem.querySelector("#" + idSelector + "-region-" + regionIndex + "-city-0").setAttribute("id", idSelector + "-region-" + regionIndex + "-city-" + (selectionItems.length));
    regionDdSec.appendChild(clonedItem);
    $(deleteBtn).on("click", deleteHandler);
    let countrySel = document.getElementById(idSelector + "-region-" + regionIndex + "-country-" + selectionItems.length),
        stateSel = document.getElementById(idSelector + "-region-" + regionIndex + "-state-" + selectionItems.length),
        citySel = document.getElementById(idSelector + "-region-" + regionIndex + "-city-" + selectionItems.length)
    countrySel.focus();
    registerCountryChange(countrySel, stateSel, citySel, regionSelection);
    registerStateChange(countrySel, stateSel, citySel, regionSelection);
    registerCityChange(citySel);
    resetDropdows(stateSel, citySel);
}

/**
 *  Clear Selection Handler
 *  @param {*} selector
 */
function clearSelectionHandler(element) {
    let filterParent = findAncestor(element, LOCATION_FILTER_CLASS);
    clearLocalSelections(filterParent);
    $(element).addClass("disabled");

    updateLocationCount(filterParent);
}

/**
 * Hides all selections
 * @param {*} selector
 */
function hideAllSelections(selector) {
    let targetItems = document.querySelectorAll(selector);
    for (let count = 0; count < targetItems.length; count++) {
        targetItems[count].style.display = "none";
    }
}

/**
 * Initialize the region with default selections
 * @param {*} regionIndex
 * @param {*} regionSelection
 */
function createNewRegionSelctionSec(regionIndex, regionSelection) {
    let firstRegion = document.getElementById(idSelector + "-region-0"),
        clonedItem = firstRegion.cloneNode(true),
        childItems = clonedItem.querySelectorAll("li");
    for (let count = 1; count < childItems.length; count++) {
        clonedItem.removeChild(childItems[count]);
    }

    clonedItem.setAttribute("id", idSelector + "-region-" + regionIndex);
    clonedItem.setAttribute("data-region-name", regionSelection);
    $(clonedItem).find('select[name=country]').attr('aria-label', 'Region ' + regionSelection);
    $(clonedItem).find('select[name=state]').attr('aria-label', 'All States');
    $(clonedItem).find('select[name=city]').attr('aria-label', 'All Cities');
    let countrySel = clonedItem.querySelector("[name='country']"),
        stateSel = clonedItem.querySelector("[name='state']"),
        citySel = clonedItem.querySelector("[name='city']");
    countrySel.setAttribute("id", idSelector + "-region-" + regionIndex + "-country-0");
    stateSel.setAttribute("id", idSelector + "-region-" + regionIndex + "-state-0");
    citySel.setAttribute("id", idSelector + "-region-" + regionIndex + "-city-0");
    let previousItem;
    for (let count = 1; count <= regionIndex; count++) {
        if (document.getElementById(idSelector + "-region-" + (regionIndex - count)) !== null) {
            previousItem = document.getElementById(idSelector + "-region-" + (regionIndex - count));
            break;
        }
    }
    //previousItem.after(clonedItem);
    previousItem.parentNode.insertBefore(clonedItem, previousItem.nextSibling);
    clonedItem.style.display = "block";
    populateCountries(countrySel, stateSel, citySel, regionSelection);
    registerCountryChange(countrySel, stateSel, citySel, regionSelection);
    registerStateChange(countrySel, stateSel, citySel, regionSelection);
    registerCityChange(citySel);
    resetDropdows(stateSel, citySel);
    locationFilterAlignment(0, idSelector);
}

/**
 * Delete Handler
 * @param {*} event
 */
function deleteHandler(event) {
    event.preventDefault();
    let ancestor = event.target.parentElement.parentElement,
        regionId = ancestor.getAttribute("id");
    event.target.parentElement.remove();
    let dropdownItems = ancestor.querySelectorAll("li");
    if (dropdownItems.length > 1) {
        for (let count = 0; count < dropdownItems.length; count++) {
            dropdownItems[count].querySelector("[name='country'").setAttribute("id", regionId + "-country-" + count);
            dropdownItems[count].querySelector("[name='state'").setAttribute("id", regionId + "-state-" + count);
            dropdownItems[count].querySelector("[name='city'").setAttribute("id", regionId + "-city-" + count);
        }
    }
    let filterParent = findAncestor(ancestor, LOCATION_FILTER_CLASS);
    updateLocationCount(filterParent);
}

/**
 * Registers onchange event on country change
 * @param {*} countrySel
 * @param {*} stateSel
 * @param {*} citySel
 * @param {*} regionSelection
 */
function registerCountryChange(countrySel, stateSel, citySel, regionSelection) {
    countrySel.onchange = function () {
        let filterParent = findAncestor(this, LOCATION_FILTER_CLASS),
            countrySelection = this.value;
        if (getActualLocationSelections(filterParent).length > 0) {
            enableClearSelections(countrySel);
            setAnalyticsOnDoneBtn(filterParent);
        }
        resetDropdows(stateSel, citySel);
        if (idSelector !== "events")
            populateStates(stateSel, citySel, regionSelection, countrySelection);

        updateLocationCount(filterParent);
    };
}

/**
 * Registers onchange event on state change
 * @param {*} countrySel
 * @param {*} stateSel
 * @param {*} citySel
 * @param {*} regionSelection
 */
function registerStateChange(countrySel, stateSel, citySel, regionSelection) {
    stateSel.onchange = function () {
        citySel.style.display = 'none';
        let stateSelection = this.value,
            countrySelection = countrySel.value,
            filterParent = findAncestor(this, LOCATION_FILTER_CLASS);
        setAnalyticsOnDoneBtn(filterParent);
        populateCities(citySel, stateSelection, countrySelection, regionSelection);
    }
}

/**
 * Registers onchange event on city change
 * @param {*} citySel
 */
function registerCityChange(citySel) {
    citySel.onchange = function () {
        let filterParent = findAncestor(this, LOCATION_FILTER_CLASS);
        setAnalyticsOnDoneBtn(filterParent);
    }
}

/**
 * Populate coutries on the country dropdown
 * @param {*} countrySel
 * @param {*} stateSel
 * @param {*} citySel
 * @param {*} regionSelection
 */
function populateCountries(countrySel, stateSel, citySel, regionSelection) {
    countrySel.length = 2;
    stateSel.length = 1;
    citySel.length = 1;

    regionData[regionSelection].values.sort(function (a, b) {
        return a.name.localeCompare(b.name);
    });
    for (let key in regionData[regionSelection].values) {
        countrySel.options[countrySel.options.length] = new Option(regionData[regionSelection].values[key].text.replace("Korea, Republic of", "Republic of Korea"), regionData[regionSelection].values[key].name);
    }
    countrySel.value = countrySel.options[0].value;
}

/**
 * Populate states on the state dropdown
 * @param {*} stateSel
 * @param {*} citySel
 * @param {*} regionSelection
 * @param {*} countrySelection
 */
function populateStates(stateSel, citySel, regionSelection, countrySelection) {
    stateSel.length = 1;
    citySel.length = 1;
    let searchResults = regionData[regionSelection].values.filter(function (state) {
        return state.name.indexOf(countrySelection) > -1;
    });
    if (searchResults.length < 1) return;
    searchResults[0].values.sort(function (a, b) {
        return a.name.localeCompare(b.name);
    });
    for (let key in searchResults[0].values) {
        stateSel.options[stateSel.options.length] = new Option(searchResults[0].values[key].text, searchResults[0].values[key].name);
    }
    if (searchResults[0].values) {
        if (searchResults[0].values.length < 2) {
            stateSel.style.display = 'none';
            populateCities(citySel, searchResults[0].values[0].name, countrySelection, regionSelection, regionData);
        } else {
            stateSel.style.display = 'block';
        }
    } else stateSel.style.display = 'none';
}

/**
 * Populate cities on the city dropdown
 * @param {*} citySel
 * @param {*} stateSelection
 * @param {*} countrySelection
 * @param {*} regionSelection
 */
function populateCities(citySel, stateSelection, countrySelection, regionSelection) {
    citySel.length = 1;
    let countrySearchResults = regionData[regionSelection].values.filter(function (state) {
        return state.name.indexOf(countrySelection) > -1;
    });
    if (countrySearchResults.length < 1) return;
    let stateSearchResults = countrySearchResults[0].values.filter(function (city) {
        return city.name.indexOf(stateSelection) > -1;
    });
    stateSearchResults[0].values.sort(function (a, b) {
        return a.name.localeCompare(b.name);
    });
    for (let key in stateSearchResults[0].values) {
        citySel.options[citySel.options.length] = new Option(stateSearchResults[0].values[key].text, stateSearchResults[0].values[key].name);
    }
    if (stateSearchResults[0].values) {
        stateSearchResults[0].values.length < 2 ? citySel.style.display = 'none' : citySel.style.display = 'block';
    } else citySel.style.display = 'none';
}

/**
 * Dynamically create Delete button
 */
function createDeleteButton() {
    let button = document.createElement("button");
    button.setAttribute("class", "remove-selection");
    button.innerText = "Remove Selection";
    return button;
}

/**
 * Finds out the ancestor of the current element based on the class name
 * @param {*Current element} el
 * @param {*Ancestor className} cls
 */
function findAncestor(el, cls) {
    while ((el = el.parentElement) && !el.classList.contains(cls)) ;
    return el;
}

/**
 * Fetch dropdown json
 * @param {*} jsonPath
 */
function fetchDropdownJson(jsonPath) {
    var jsonData;
    $.ajax({
        url: jsonPath,
        type: "GET",
        async: false,
        success: function (result) {
            jsonData = result;
        }
    });
    return jsonData;
}

/**
 * Reset State and City dropdowns
 * @param {*} stateSel
 * @param {*} citySel
 */
function resetDropdows(stateSel, citySel) {
    stateSel.style.display = 'none';
    citySel.style.display = 'none';
}

/**
 * Enable the Clear Selection Button
 * @param {*} element
 */
function enableClearSelections(element) {
    let ancestor = findAncestor(element, LOCATION_FILTER_CLASS),
        clearButton = ancestor.querySelector("." + PARENT_CLEAR_SELECTION_CLASS);
    clearButton.className = PARENT_CLEAR_SELECTION_CLASS;
    $(clearButton).attr('tabindex', '0');
}

/**
 * Clear All the selections of all the selected regions

 * @param {*} element
 */
function clearAllSelections(element) {
    let selectedRegions = element.querySelectorAll("ul.location-selection-section"),
        regionContainer = document.getElementById(idSelector + "-regions"),
        regionListParent = regionContainer.querySelector("ul");
    if (selectedRegions.length > 1) {
        for (let count = 1; count < selectedRegions.length; count++) {
            //Remove other region selections
            selectedRegions[count - 1].remove();
        }
    }

    removeSecondarySelections(selectedRegions[0]);
    selectedRegions[0].style.display = "block";
    if (regionListParent) regionListParent.remove();
}

function clearAllMobileSelections(element) {
    let regions = element.querySelectorAll(".region-item");
    for (let count = 0; count < regions.length; count++) {

        removeSecondarySelections(regions[count]);
        resetPrimarySelections(regions[count]);
    }
}

/**
 * Clear all the selections under particular region
 * @param {*} element
 */
function clearLocalSelections(element) {
    //let selectedRegionName = element.querySelector(".region-item.active").textContent.trim(),
    let selectedRegionName = $(".region-item.active").attr("value"),
        targetRegion = element.querySelector("[data-region-name='" + selectedRegionName + "']");
    if (!targetRegion) return;
    removeSecondarySelections(targetRegion);
    resetPrimarySelections(targetRegion);
}

/**
 * Remove all the secondary selections under particular region
 * @param {*} region
 */
function removeSecondarySelections(region) {
    let allSelections = region.querySelectorAll("li");
    if (allSelections.length > 1) {
        for (let count = 1; count < allSelections.length; count++) {

            allSelections[count].remove();
        }
    }
}

/**
 * Reset primary selection
 * @param {*} region
 */
function resetPrimarySelections(region) {
    let selection = region.querySelector("li");
    if (!selection) return;
    let countrySel = selection.querySelector("[name='country']"),
        stateSel = selection.querySelector("[name='state']"),
        citySel = selection.querySelector("[name='city']");
    countrySel.selectedIndex = 0;
    stateSel.selectedIndex = 0;
    citySel.selectedIndex = 0;
    resetDropdows(stateSel, citySel);
}

/**
 * Update the location count
 * @param {*} element
 */
function updateLocationCount(element) {
    if (idSelector === 'events') {
        RESULTSET_CLASS = 'cmp-events--filter--resultset';
    }
    let parent = findAncestor(element, RESULTSET_CLASS),
        locationLabel = parent.querySelector("." + LOCATION_LABEL_CLASS),
        actualSelectionsLength = getActualLocationSelections(element).length;
    if (actualSelectionsLength > 0) {
        locationLabel.innerText = "Location (" + actualSelectionsLength + " selected)";
        parent.querySelector("." + CLEAR_ALL_BUTTON_CLASS).classList.remove("disabled");
        parent.querySelector("." + CLEAR_ALL_BUTTON_CLASS).setAttribute("tabindex", "0");
        parent.querySelector(".clearSelection").setAttribute("tabindex", "0");
    } else {
        $(".clearSelection").attr("tabindex", -1).blur();
        locationLabel.innerText = "Location";
    }
}

/**
 * Get the actual selections list
 * @param {*} element
 */
function getActualLocationSelections(element) {
    let totalSelections = element.querySelectorAll("[name='country']"),
        selections = [];
    for (let count = 0; count < totalSelections.length; count++) {
        if (totalSelections[count].value !== "select-any-country") selections.push(totalSelections[count]);
    }
    return selections;
}

/**
 * Capitalizes the String
 * @param {*} targetString
 */
function capitalizeString(targetString) {
    if (typeof targetString !== 'string') return '';
    let splittedItems = targetString.split(" "), finalString = "";
    for (let count = 0; count < splittedItems.length; count++) {
        splittedItems[count] = splittedItems[count].toLowerCase();
        finalString = finalString + splittedItems[count].charAt(0).toUpperCase() + splittedItems[count].slice(1) + " ";
    }
    return finalString.trim(finalString.charAt(finalString.length));
}

function setAnalyticsOnDoneBtn(filterParent) {

    if (idSelector === 'events') {
        RESULTSET_CLASS = 'cmp-events--filter--resultset';
    }
    let filterDoneBtn = findAncestor(filterParent, RESULTSET_CLASS).querySelector(FILTER_DONE_BTN_SELECTOR);
    if (filterDoneBtn) {
        let filterLabel = findAncestor(filterParent, ACCORDION_WRAPPER_CLASS).querySelector("." + FILTER_LABEL_CLASS).textContent.trim(),
            flowType = experienceHire ? "Experienced Professionals" : "S&G";
        filterLabel = filterLabel.indexOf("(") !== -1 ? filterLabel.slice(0, filterLabel.indexOf("(")).trim() : filterLabel;
        filterDoneBtn.setAttribute("data-analytics-link", "Careers Search Filter | " + flowType + " | " + filterLabel + " | Done");
        filterDoneBtn.setAttribute("data-analytics-button", "Careers Search Filter | " + flowType + " | " + filterLabel + " | Done");
    }
}
var ACCORDION_WRAPPER_CLASS = "acc-wrap",
    FILTER_LABEL_CLASS = "filter-label",
    LOCATION_FILTER_CLASS = "accordion--location__filters",
    FILTER_DONE_BTN_SELECTOR = ".cmp-opportunity--filter--resultset .filter-done a",
    enteredKeyword;
$(document).ready(function () {
    const oppAggComp = $(".cmp-opportunity-aggregate");
    if(oppAggComp == undefined && oppAggComp.length <=0 ){
        return;
    }
    var checkboxes = document.querySelectorAll(".cmp-opportunity--filter--resultset .checkbox input");

    for (let count = 0; count < checkboxes.length; count++) {
        checkboxes[count].addEventListener('change', function (event) {
            let parentWrapper = findAncestor(event.target, ACCORDION_WRAPPER_CLASS),
                filterLabel = parentWrapper.querySelector("." + FILTER_LABEL_CLASS).textContent.trim(),
                filterDoneBtn = findAncestor(parentWrapper, RESULTSET_CLASS).querySelector(FILTER_DONE_BTN_SELECTOR),
                flowType = experienceHire ? "Experienced Professionals" : "S&G";
            filterLabel = filterLabel.indexOf("(") !== -1 ? filterLabel.slice(0, filterLabel.indexOf("(")).trim() : filterLabel;
            if (filterDoneBtn) {
                filterDoneBtn.setAttribute("data-analytics-link", "Careers Search Filter | " + flowType + " | " + filterLabel + " | Done");
                filterDoneBtn.setAttribute("data-analytics-button", "Careers Search Filter | " + flowType + " | " + filterLabel + " | Done");
            }
        });
    }

    var getJobQuery = getUrlParameter('opportunity');
    var getBusinessArea = getUrlParameter('businessarea');
    var getProgramType = getUrlParameter('programtype');
    var getEducationLevel = getUrlParameter('educationlevel');
    var getRegion = getUrlParameter('region');
    var getCountry = getUrlParameter('country');
    var getState = getUrlParameter('state');
    var getCity = getUrlParameter('city');
    opportunityValue = getJobQuery.toLowerCase();

    var loacationRegionValue = getRegion.split(',');
    var loacationCountryValue = getCountry.split(',');
    var loacationStateValue = getState.split(',');
    var loacationCityValue = getCity.split(',');
    let queryParamaters = {
        region: loacationRegionValue,
        country: loacationCountryValue,
        state: loacationStateValue,
        city: loacationCityValue
    };

    experienceHire = true;
    epBackButtonFlag = true;
    sgBackButtonFlag = true;
    if (opportunityValue === "ep") {
        var queryParamsValue = {
            'institutional-securities': 'CCH-10100',
            'operations': 'CCH-10200',
            'company': 'CCH-10300',
            'investment-management': 'CCH-10600',
            'wealth-management': 'CCH-10910',
            'firm-resilience-cyber-and-gic': 'CCH-92175',
            'technology': 'CCH-98100'
        };

        var getBAQueryParam = getBusinessArea.split(';');
        getBusinessArea = "";

        for (i = 0; i < getBAQueryParam.length; i++) {
            let convertValue = getBAQueryParam[i];
            getBusinessArea += queryParamsValue[convertValue] + ';';
        }
    }

    if (opportunityValue === "sg") {
        var sgQueryParamsValue = {
            'institutional-securities-group': 'Institutional Securities Group',
            'operations': 'Operations',
            'company': 'Company',
            'investment-management': 'Investment Management',
            'wealth-management': 'Wealth Management',
            'technology': 'Technology'
        };

        var getSGBAQueryParam = getBusinessArea.split(';');
        getBusinessArea = "";

        for (i = 0; i < getSGBAQueryParam.length; i++) {
            let convertValue = getSGBAQueryParam[i];
            getBusinessArea += sgQueryParamsValue[convertValue] + ';';
        }
    }

    function jobsPromptInitialize(getBusinessArea, getProgramType, getEducationLevel, loacationRegionValue, loacationCountryValue, loacationStateValue, loacationCityValue) {

        if (getBusinessArea || getProgramType || getEducationLevel || loacationRegionValue || loacationCountryValue || loacationStateValue || loacationCityValue) {
            $(".cmp-opportunity--findjobs").hide();

            $(".cmp-opportunity--filter--resultset").removeClass("slick--enabled");
            $(".cmp-opportunity--filter--resultset").prev().hide();
            $(".helpUs, .what--looking--title").hide();
            $(".accordion--filterby__wrapper, .accordion--joblevel__wrapper, .filter-done").show();
            $(".cmp-opportunity--filter--resultset").css("display", "-ms-grid").css("display", "grid");
            $(".cmp-opportunity--filter--resultset.studentsandgrads").hide();
            experienceHire = true;

            bindAccordioCheckBox();
            switchToProgram();

            var businessAreaValues = getBusinessArea.split(';');

            $.each(businessAreaValues, function (i, val) {
                $("input[value*='" + val + "']").click();
            });

            var programTypeValues = getProgramType.split(';');
            $.each(programTypeValues, function (i, val) {
                $("input[value*='" + val + "']").click();
            });

            var educationLevelValues = getEducationLevel.split(';');
            $.each(educationLevelValues, function (i, val) {
                $("input[value*='" + val + "']").click();

            });

            var getQueryParams = getSelectedValues();
            let collectedLoacations = getQueryParamaters(queryParamaters, 'sg');

            createRequestResultSet(getQueryParams + '&location=' + collectedLoacations);

        } else {
            opportunityValue = "ep";

            if (epBackButtonFlag) {
                $(".experienceHire .cmp-opportunity--filter__slick").on('init reInit afterChange', function (event, slick, currentSlide, nextSlide) {
                    var i = (currentSlide ? currentSlide : 0) + 1;
                    $(".careerTypesCount").text((i) + ' of ' + (slick.slideCount));

                    if (i == slick.slideCount) {
                        $(".doneButton").show();
                        $(".nextButton").hide();
                    } else {
                        //$(".nextButton").show();
                        $(".doneButton").hide();
                    }
                    if (i !== 1) {
                        $('.backButton').show().focus();
                    }
                    if (currentSlide == 1) {
                        $(".nextButton").show();
                    }
                });

                $(".experienceHire .cmp-opportunity--filter__slick").on('beforeChange', function (event, slick, currentSlide, nextSlide) {
                    if (nextSlide == 0) {
                        // $(".experienceHire .cmp-opportunity--filter__slick").slick('unslick');
                        epBackButtonFlag = false;
                        $(".cmp-opportunity--filter--resultset.experienceHire.slick--enabled, .careerTypesCount, .nextButton, .backButton").hide();
                        $(".cmp-opportunity--quicksearch, .backButton").show();
                        $(".backButton").attr("data-analytics-button", "Career Opportunities | Back");
                        $(".helpUs, .what--looking--title").hide();
                    }
                });

                $(".cmp-opportunity--findjobs, .what--looking--title").hide();
                $(".cmp-opportunity--filter--resultset.experienceHire").css("display", "-ms-grid").css("display", "grid");
                $(".cmp-opportunity--filter--resultset.studentsandgrads").hide();
                $(".careerTypesCount, #careerWrapper, .nextButton").show();
                $(".accordion--filterby__wrapper, .accordion--joblevel__wrapper, .filter-done").hide();
                $(".cmp-opportunity--filter--resultset.experienceHire.slick--enabled").css("display", "grid");
                experienceHire = true;
                $(".experienceHire .cmp-opportunity--filter__slick").slick({
                    slidesToShow: 1,
                    slidesToScroll: 1,
                    dots: true,
                    speed: 500,
                    fade: true,
                    adaptiveHeight: false,
                    infinite: false,
                    arrows: false
                });
                if (!sgBackButtonFlag) {
                    var filterClass = ['div#slick-slide10, div#slick-slide11, div#slick-slide12'].join(',');
                } else {
                    var filterClass = ['div#slick-slide00, div#slick-slide01, div#slick-slide02'].join(',');
                }
                $(".experienceHire .cmp-opportunity--filter__slick").slick('slickFilter', filterClass);
                $(".experienceHire .cmp-opportunity--filter__slick").slick('slickGoTo', 1);
            } else {
                $(".cmp-opportunity--filter--resultset.experienceHire.slick--enabled, .careerTypesCount, .nextButton, .backButton").show();
                $(".cmp-opportunity--findjobs").hide();
                $(".cmp-opportunity--filter--resultset.experienceHire .cmp-opportunity--filter__slick").slick('slickGoTo', 1);
            }
        }

    }

    function internPromptInitialize(getBusinessArea, getProgramType, getEducationLevel, loacationRegionValue, loacationCountryValue, loacationStateValue, loacationCityValue) {

        if (getBusinessArea || getProgramType || getEducationLevel || loacationRegionValue || loacationCountryValue || loacationStateValue || loacationCityValue) {
            $(".cmp-opportunity--findjobs").hide();

            $(".cmp-opportunity--filter--resultset").removeClass("slick--enabled");
            $(".cmp-opportunity--filter--resultset").prev().hide();
            $(".helpUs, .what--looking--title").hide();
            $(".accordion--filterby__wrapper, .accordion--joblevel__wrapper, .filter-done").show();
            $(".cmp-opportunity--filter--resultset").css("display", "-ms-grid").css("display", "grid");
            $(".cmp-opportunity--filter--resultset.experienceHire").hide();
            $(".cmp-opportunity--filter--resultset.studentsandgrads").css("display", "grid");
            experienceHire = false;

            bindAccordioCheckBox();
            switchToProgram();

            var arrayValues = getBusinessArea.split(';');
            $.each(arrayValues, function (i, val) {
                if ($("input[value='" + val + "']").length > 1) {
                    $($("input[value='" + val + "']")[0]).click();
                } else {
                    $("input[value='" + val + "']").click();
                }

            });


            var programTypeValues = getProgramType.split(';');
            $.each(programTypeValues, function (i, val) {
                $("input[value*='" + val + "']").click();
            });

            var educationLevelValues = getEducationLevel.split(';');
            $.each(educationLevelValues, function (i, val) {
                $("input[value*='" + val + "']").click();

            });

            var getQueryParams = getSelectedValues();
            let collectedLoacations = getQueryParamaters(queryParamaters, 'ep');

            createRequestResultSet(getQueryParams + '&location=' + collectedLoacations);

        } else {
            opportunityValue = "sg";
            $(".what--looking--title").hide();

            if (sgBackButtonFlag) {
                $(".cmp-opportunity--filter--resultset.studentsandgrads .cmp-opportunity--filter__slick").on('init reInit afterChange', function (event, slick, currentSlide, nextSlide) {
                    var i = (currentSlide ? currentSlide : 0) + 1;
                    $(".careerTypesCount").text((i) + ' of ' + (slick.slideCount));

                    if (i == slick.slideCount) {
                        $(".doneButton").show();
                        $(".nextButton").hide();
                    } else {
                        $(".nextButton").show();
                        $(".doneButton").hide();
                    }
                    if (i !== 1) {
                        $('.backButton').show().focus();
                    }
                });

                $(".cmp-opportunity--filter--resultset.studentsandgrads .cmp-opportunity--filter__slick").on('beforeChange', function (event, slick, currentSlide, nextSlide) {
                    if (nextSlide == 0) {
                        // $(".studentsandgrads .cmp-opportunity--filter__slick").slick('unslick');
                        sgBackButtonFlag = false;
                        $(".cmp-opportunity--filter--resultset.studentsandgrads.slick--enabled, .careerTypesCount, .nextButton, .backButton").hide();
                        $(".cmp-opportunity--quicksearch, .backButton").show();
                        $(".backButton").attr("data-analytics-button", "Career Search Flow | S&G | Quick Search | Back");
                        $(".helpUs, .what--looking--title").hide();
                    }
                });

                $(".cmp-opportunity--findjobs").hide();
                $(".cmp-opportunity--filter--resultset.experienceHire").hide();
                $(".cmp-opportunity--filter--resultset.studentsandgrads").css("display", "-ms-grid").css("display", "grid");
                $(".careerTypesCount, #careerWrapper, .nextButton").show();
                $(".accordion--filterby__wrapper, .accordion--joblevel__wrapper, .filter-done").hide();
                $(".cmp-opportunity--filter--resultset.experienceHire.slick--enabled").hide();
                $(".cmp-opportunity--filter--resultset.studentsandgrads.slick--enabled").css("display", "grid");
                experienceHire = false;

                $(".cmp-opportunity--filter--resultset.studentsandgrads .cmp-opportunity--filter__slick").slick({
                    slidesToShow: 1,
                    slidesToScroll: 1,
                    dots: true,
                    speed: 500,
                    fade: true,
                    adaptiveHeight: false,
                    infinite: false,
                    arrows: false
                });

                if (!epBackButtonFlag) {
                    var filterClassSg = ['div#slick-slide10, div#slick-slide11, div#slick-slide12, div#slick-slide13, div#slick-slide14'].join(',');
                } else {
                    var filterClassSg = ['div#slick-slide00, div#slick-slide01, div#slick-slide02, div#slick-slide03, div#slick-slide04'].join(',');
                }
                $(".cmp-opportunity--filter--resultset.studentsandgrads .cmp-opportunity--filter__slick").slick('slickFilter', filterClassSg);
                $(".cmp-opportunity--filter--resultset.studentsandgrads .cmp-opportunity--filter__slick").slick('slickGoTo', 1);
            } else {
                $(".cmp-opportunity--filter--resultset.studentsandgrads.slick--enabled, .careerTypesCount, .nextButton, .backButton").show();
                $(".cmp-opportunity--findjobs").hide();
                $(".cmp-opportunity--filter--resultset.studentsandgrads .cmp-opportunity--filter__slick").slick('slickGoTo', 1);
            }
        }
    }

    switch (getJobQuery.toLowerCase()) {
        case 'ep':
            jobsPromptInitialize(getBusinessArea, getProgramType, getEducationLevel, loacationRegionValue, loacationCountryValue, loacationStateValue, loacationCityValue);
            break;
        case 'sg':
            internPromptInitialize(getBusinessArea, getProgramType, getEducationLevel, loacationRegionValue, loacationCountryValue, loacationStateValue, loacationCityValue);
            break;
    }


    //A11Y

    $(".clearSelection").on('keydown', function (event) {
        var keyCode = event.keyCode || event.which;
        if (keyCode == 13 || event.keyCode == 27) {
            $(this).click().blur();
        }
    });

    $('.backButton').click(function (e) {
        e.preventDefault();
        if ($('.cmp-opportunity--quicksearch:visible').length == 1 && experienceHire) {
            $(".cmp-opportunity--quicksearch").hide();
            $(".what--looking--title, .helpUs").show();
            $(".cmp-opportunity--regionSelectors").show();
            return false;
            // initAnalyticsHandler();
        }
        if ($('.cmp-opportunity--quicksearch:visible').length == 1 && !experienceHire) {
            $(".cmp-opportunity--quicksearch").hide();
            $('.backButton').hide();
            $(".cmp-opportunity--findjobs, .what--looking--title, .helpUs").show();
            // initAnalyticsHandler();
        }
        if ($('.cmp-opportunity--regionSelectors:visible').length == 1) {
            $(".cmp-opportunity--regionSelectors").hide();
            $(".what--looking--title").html("What are you looking?");
            $('.backButton').hide();
            $(".cmp-opportunity--findjobs, .what--looking--title, .helpUs").show();
            // initAnalyticsHandler();
        }
        $(this).parent().find('.slick-slider').slick('slickPrev');
        initAnalyticsHandler();

    });

    $('.nextButton').click(function (e) {
        e.preventDefault();
        $(this).parent().find('.slick-slider').slick('slickNext');
        $('.backButton').show();
        initAnalyticsHandler();
    });


    $(".career-type-division li").click(function () {
        $(".career-type-division-level-1").show();
        let getItemIndex = $(this).index();
        $(this).parent().siblings(".career-type-division-level-1").children("ul").hide();
        $($(this).parent().siblings(".career-type-division-level-1").children("ul")[getItemIndex]).show();
        $('.career-type-division li').css("opacity", "1");
        $(this).siblings().css("opacity", "0.5");
    })

    $(".career-type li").click(function () {
        let getItemIndex = $(this).index();
        $("#careerWrapper").slick('slickGoTo', getItemIndex + 1);
    })


    //  $(".findJob").click(function(e) {
    $(".job-experience").click(function (e) {
        e.preventDefault();
        $(".cmp-opportunity--findjobs").hide();
        $(".what--looking--title").text("Where are you looking?");
        regionSelectorsSection("ep");
        //jobsPromptInitialize();
        //initAnalyticsHandler();
    });


    $(".intern-students").click(function (e) {
        e.preventDefault();
        $(".cmp-opportunity--findjobs").hide();
        quicksearchSeaction("sg");

        // internPromptInitialize();
        // initAnalyticsHandler();
    });


    function regionSelectorsSection(type) {
        $(".cmp-opportunity--regionSelectors").show();
        let backButton = document.querySelector(".backButton");
        let searchType = experienceHire ? "Experienced Professionals" : "S&G";
        $(".title-for").html();
        $(".backButton").show().focus();
        backButton.setAttribute("data-analytics-button", "Career Opportunities | " + backButton.textContent.trim());
    }

    $(".region-russiaChina").click(function (e) {
        e.preventDefault();
        $(".cmp-opportunity--regionSelectors").hide();
        quicksearchSeaction("ep");
    });


    // Quick search funtion
    function quicksearchSeaction(type) {
        let backButton = document.querySelector(".backButton");
        let searchType = experienceHire ? "Experienced Professionals" : "S&G";
        $(".title-for").html();
        $(".backButton").show().focus();
        $(".cmp-opportunity--quicksearch").show();
        $(".helpUs").hide();
        $(".what--looking--title").hide();
        $(".button--guidedsearch_ep, .button--guidedsearch_sg").hide();
        $(".button--quicksearch-sg, .button--quicksearch-ep").hide();
        if (type === "ep") {
            experienceHire = true;
            searchType = "Experienced Professionals";
            $(".title-for").html("Opportunities for Experienced Professionals.")
            $(".button--guidedsearch_ep, .button--quicksearch-ep").show();
        }
        if (type === "sg") {
            experienceHire = false;
            searchType = "S&G";
            $(".title-for").html("Opportunities for Student and Graduates.")
            $(".button--guidedsearch_sg, .button--quicksearch-sg").show();
        }
        backButton.setAttribute("data-analytics-button", "Career Search Flow | " + searchType + " | Quick Search | " + backButton.textContent.trim());
        $(".button--guidedsearch_ep").click(function (e) {
            $(".cmp-opportunity--quicksearch").hide();
            $(".helpUs").show();
            $(".what--looking--title").hide();
            e.preventDefault();
            jobsPromptInitialize();
            initAnalyticsHandler();
        })

        $(".button--guidedsearch_sg").click(function (e) {
            $(".cmp-opportunity--quicksearch").hide();
            $(".helpUs").show();
            $(".what--looking--title").hide();
            e.preventDefault();
            internPromptInitialize();
            initAnalyticsHandler();
        })
    }


    //Check the opprtunity and fetch result set
    if (experienceHire) {
        expHireResutSet();
    } else {
        studGrandsResutSet();
    }

    $('.cmp-opportunity--filter--resultset .resultsSort').click(function (e) {
        e.preventDefault();
        if ($(this).find('span').hasClass('sort-down')) {
            $(this).find('a').attr('aria-label', 'descending order by date')
        } else {
            $(this).find('a').attr('aria-label', 'ascending order by date')
        }
        $(this).find('span').toggleClass('sort-down');
        resultSet.resultSet.reverse();
        generateResult(resultSet, currentPage, enteredKeyword, "noautoselect");
    });


    $('.keyword-search-wrapper input').on('keyup', function (e) {
        var labelText = $(this).next('label');
        if (this.value !== '') {
            labelText.addClass("input-has-val");
            checkCharLength(this.value, e.which);
        } else {
            labelText.removeClass("input-has-val");
            checkCharLength(this.value, e.which);
        }
    })

    function checkCharLength(keyword, keyCode) {
        if ((keyword.length < 3 && keyCode == 13) || (keyword == '' && keyCode == 13)) {
            $(".keyword-search-err-msg").addClass('keyword-show-err').text("Please enter 3 or more characters");
            $(".keyword-search-err-msg").attr("aria-invalid", "true");
            $(".keyword-search-err-msg").attr("aria-hidden", "false");
            let errorMsgId = $(".keyword-search-err-msg").attr("id");
            $(".keyword-search-wrapper input").attr("aria-describedby", errorMsgId).addClass('keyword-show-err');
            $(".keyword-search-wrapper input").focus();
        } else {
            $(".keyword-search-wrapper input").removeClass('keyword-show-err').removeAttr("aria-describedby");
            $(".keyword-search-err-msg").removeClass('keyword-show-err');
            $(".keyword-search-err-msg").attr("aria-invalid", "false");
            $(".keyword-search-err-msg").attr("aria-hidden", "true");
            if (keyword.length > 2 && keyCode == 13) {
                $('.button--quicksearch').trigger("click");
            }
        }
    }

    $('.button--quicksearch').click(function (e) {
        e.preventDefault();
        enteredKeyword = $(".quicksearch input").val().trim();
        /* sanitize keyword for XSS attack */
        //enteredKeyword = enteredKeyword.replace(/[^\w\s]/gi, "");
        let format = /[!@#$%^&*()_+¬`£~%\-=\[\]{};':"\\|,.<>\/?]+/;

        if (format.test(enteredKeyword)) {
            $(".keyword-search-err-msg").addClass('keyword-show-err').text("Please enter alphanumeric keyword");
            $(".keyword-search-err-msg").attr("aria-invalid", "true");
            let errorMsgId = $(".keyword-search-err-msg").attr("id");
            $(".keyword-search-wrapper input").attr("aria-describedby", errorMsgId).addClass('keyword-show-err');
            $(".keyword-search-wrapper input").focus();
            return false;
        }

        if (enteredKeyword.length < 3) {
            $(".keyword-search-err-msg").addClass('keyword-show-err').text("Please enter 3 or more characters");
            $(".keyword-search-err-msg").attr("aria-invalid", "true");
            let errorMsgId = $(".keyword-search-err-msg").attr("id");
            $(".keyword-search-wrapper input").attr("aria-describedby", errorMsgId).addClass('keyword-show-err');
            $(".keyword-search-wrapper input").focus();
            return false;
        }

        onClickFetchResult(enteredKeyword);
    });

    $('.all--exp--link, .all--sg--link').click(function (e) {
        e.preventDefault();
        onClickFetchResult();
// Clear All Filters
        $(".accordion--filterby__clear").click();
        $(".cmp-opportunity--filter--resultset").removeClass("active");
        $(".cmp-opportunity--result__set").addClass("cmp-opportunity--result__set--expand");

    });


    bindAccordioCheckBox();

});

function onClickFetchResult(keyword) {

    $(".cmp-opportunity--filter__slick.slick-initialized").slick('slickUnfilter');
    $(".cmp-opportunity--filter__slick.slick-initialized").slick('unslick');
    $(".cmp-opportunity--filter--resultset").removeClass("slick--enabled");
    $(".cmp-opportunity--filter--resultset").prev().hide();
    $(".helpUs, .what--looking--title, .cmp-opportunity--quicksearch").hide();
    $(".accordion--filterby__wrapper, .accordion--joblevel__wrapper, .filter-done").show();
    $(".nextButton, .doneButton").hide();
    if (window.innerWidth <= 1024) {
        $(".floatingMenu").show();
        $(".cmp-opportunity--filter__accordion").hide();
    }
    if (experienceHire) {
        $(".cmp-opportunity--filter--resultset.experienceHire").css("display", "-ms-grid").css("display", "grid");
        $(".cmp-opportunity--filter--resultset.studentsandgrads").hide();

        expHireResutSet();
        //   collectedParameterValues = '&opportunity=ep&lang=en';
        collectedParameterValues = $(".button--go").attr("results-parameter");
        createRequestResultSet(collectedParameterValues, keyword);

    } else {
        $(".cmp-opportunity--filter--resultset.experienceHire").hide();
        $(".cmp-opportunity--filter--resultset.studentsandgrads").css("display", "-ms-grid").css("display", "grid");
        studGrandsResutSet();

        collectedParameterValues = '&opportunity=sg&lang=en';
        createRequestResultSet(collectedParameterValues, keyword);
    }

    bindAccordioCheckBox();
    switchToProgram();

    // Clear All Filters
    $(".accordion--filterby__clear").click();
    $(".cmp-opportunity--filter--resultset").removeClass("active");
    $(".cmp-opportunity--result__set").addClass("cmp-opportunity--result__set--expand");
}

function initAnalyticsHandler() {
    let searchType, activeSlick, filterLabel, resultSetParent, nextButton, backButton, doneButton, allButton;
    searchType = experienceHire ? "Experienced Professionals" : "S&G";
    activeSlick = document.querySelector(".cmp-opportunity--filter--resultset .slick-current.slick-active");
    filterLabel = activeSlick.querySelector("." + FILTER_LABEL_CLASS).textContent.split('(')[0].trim();
    resultSetParent = findAncestor(activeSlick, RESULTSET_CLASS);
    nextButton = resultSetParent.querySelector(".nextButton a");
    doneButton = resultSetParent.querySelector(".doneButton a");
    backButton = document.querySelector(".backButton");
    allButton = resultSetParent.querySelector(".all--sg--link");
    nextButton.setAttribute("data-analytics-button", "Career Search Flow | " + searchType + " | " + filterLabel + " | " + nextButton.textContent.trim());
    backButton.setAttribute("data-analytics-button", "Career Search Flow | " + searchType + " | " + filterLabel + " | " + backButton.textContent.trim());
    doneButton.setAttribute("data-analytics-button", "Career Search Flow | " + searchType + " | " + filterLabel + " | " + doneButton.textContent.trim());
    allButton.setAttribute("data-analytics-link", "Career Search Flow | " + searchType + " | " + filterLabel + " | " + allButton.textContent.trim());

}

function switchToProgram() {
    // Bind Click for switch to filter link
    $(".cmp-opportunity--filter--resultset.experienceHire .switchto__text").on('keyup', function (e) {
        if (e.keyCode === 13) {
            $(this).click();
        }
    })
    $(".cmp-opportunity--filter--resultset.experienceHire .switchto__text").on('click', function () {
        // location.reload(false);
        let url_string = location.href;
        let findWcmode = url_string.search("wcmmode=disabled");
        let findQuerry = url_string.search("opportunity");
        let url_param = url_string.split('?');

        let newurl = url_param[0];
        if (findWcmode > -1) {
            newurl = newurl + '?wcmmode=disabled';
            location.href = newurl;
        } else {
            if (findQuerry > 11)
                location.href = newurl;
            else
                location.reload(false);
        }

        /* Switch to resultset
        clearAllFilterStudGrads(this);
        if(window.innerWidth < 1024) {
            $(".floatingMenu").toggleClass("topMenu");
            $(".cmp-opportunity--filter__accordion").hide();
            $(".resultsFound").show();
        }
        studGrandsResutSet();
        */
    });
    $(".cmp-opportunity--filter--resultset.studentsandgrads .switchto__text").on('keyup', function (e) {
        if (e.keyCode === 13) {
            $(this).click();
        }
    })
    $(".cmp-opportunity--filter--resultset.studentsandgrads .switchto__text").on('click', function () {
        //  location.reload(false);

        let url_string = location.href;
        let findWcmode = url_string.search("wcmmode=disabled");
        let findQuerry = url_string.search("opportunity");
        let url_param = url_string.split('?');

        let newurl = url_param[0];
        if (findWcmode > -1) {
            newurl = newurl + '?wcmmode=disabled';
            location.href = newurl;
        } else {
            if (findQuerry > 11)
                location.href = newurl;
            else
                location.reload(false);
        }

        /* Switch to resultset
        clearAllFilterExpProf(this);
        if(window.innerWidth < 1024) {
            $(".floatingMenu").toggleClass("topMenu");
            $(".cmp-opportunity--filter__accordion").hide();
            $(".resultsFound").show();
        }
        expHireResutSet();
        */
    });


}

function expHireResutSet() {
    $(".experienceHire .filter--button--done").on('click', function (e) {
        e.preventDefault();
        opportunityValue = "ep";
        $(".accordion--header .accordion--arrow").removeClass("expand");
        $(".accordion--header").each(function (index) {
            let analyticsVal = $(this).attr("data-analytics-link");
            analyticsVal = analyticsVal.replace("Collapse", "Expand");
            $(this).attr("data-analytics-link", analyticsVal);
        });
        $(".accordion--filter--title").hide();
        $('.accordion--content').hide();
        $(".cmp-opportunity--filter--resultset").removeClass("active");
        $(".cmp-opportunity--filter--resultset.experienceHire .cmp-opportunity--result__set").addClass("cmp-opportunity--result__set--expand");

        let getLangSelection = $($(".experienceHire .accordion--jobslevel__filters li").find("input:checked")).val();
        if (getLangSelection === "FR") {
            $(".experienceHire .accordion--jobslevel__label").text("Emplois disponibles en: Francais");
        } else {
            $(".experienceHire .accordion--jobslevel__label").text("Jobs available in: English");
        }

        var getQueryParams = getSelectedValues();
        createRequestResultSet(getQueryParams);

        switchFloatingMenu();

    });
}

function studGrandsResutSet() {
    $(".studentsandgrads .filter--button--done").on('click', function (e) {
        e.preventDefault();
        opportunityValue = "sg";
        $(".accordion--header .accordion--arrow").removeClass("expand");
        $(".accordion--header").each(function (index) {
            let analyticsVal = $(this).attr("data-analytics-link");
            analyticsVal = analyticsVal.replace("Collapse", "Expand");
            $(this).attr("data-analytics-link", analyticsVal);
        });
        $('.accordion--content').hide();
        $(".accordion--filter--title").hide();
        $(".cmp-opportunity--filter--resultset").removeClass("active");
        $(".cmp-opportunity--filter--resultset.studentsandgrads .cmp-opportunity--result__set").addClass("cmp-opportunity--result__set--expand");

        let getLangSelection = $($(".studentsandgrads .accordion--jobslevel__filters li").find("input:checked")).val();
        if (getLangSelection === "FR") {
            $(".studentsandgrads .accordion--jobslevel__label").text("Emplois disponibles en: Francais");
        } else {
            $(".studentsandgrads .accordion--jobslevel__label").text("Jobs available in: English");
        }


        var getQueryParams = getSelectedValues();
        createRequestResultSet(getQueryParams);

        switchFloatingMenu();

    });
}

function switchFloatingMenu() {
    if (window.innerWidth <= 1024) {
        $(".cmp-opportunity--filter__accordion").hide();
        //$(".cmp-opportunity--result__set").toggle();
        //$(".resultsFound").toggle();
        //$(".resultsSort").toggle();
        $(".floatingMenu").toggleClass("topMenu");
    }
}

function clearAllFilterStudGrads(element) {
    idSelector = "sg";
    let resultSetParent = document.querySelector(".studentsandgrads"),
        filterParent = resultSetParent.querySelector("." + LOCATION_FILTER_CLASS);
    if (getActualLocationSelections(filterParent).length > 0) {
        if (window.innerWidth > 767) {
            clearAllSelections(filterParent);
            initLocationSet();
        } else clearAllMobileSelections(filterParent);
        filterParent.querySelector(".clearSelection").classList.add("disabled");
    }

    updateLocationCount(filterParent);

    collectedParameterValues = '&opportunity=sg&lang=en';
    experienceHire = false;
    createRequestResultSet(collectedParameterValues);

    $(".cmp-opportunity--filter--resultset.studentsandgrads").css("display", "-ms-grid").css("display", "grid");
    $(".cmp-opportunity--filter--resultset.experienceHire").hide();
    $(".accordion--filterby__clear").click();
    $(".cmp-opportunity--filter--resultset").removeClass("active");
    $(".cmp-opportunity--result__set").addClass("cmp-opportunity--result__set--expand");
    $(".accordion--header .accordion--arrow").removeClass("expand");
    $('.accordion--content').hide();
    $(".accordion--jobslevel__label").text("Jobs available in: English");
    $(".accordion--jobslevel__filters .checkbox input#English").prop('checked', true)
    $(".accordion--jobslevel__filters .checkbox input#English-sg").prop('checked', true)

}

function clearAllFilterExpProf(element) {
    idSelector = "ep";
    let resultSetParent = document.querySelector(".experienceHire"),
        filterParent = resultSetParent.querySelector("." + LOCATION_FILTER_CLASS);
    if (getActualLocationSelections(filterParent).length > 0) {
        if (window.innerWidth > 767) {
            clearAllSelections(filterParent);
            initLocationSet();
        } else clearAllMobileSelections(filterParent);
        filterParent.querySelector(".clearSelection").classList.add("disabled");
    }
    updateLocationCount(filterParent);

    //collectedParameterValues = '&opportunity=ep&lang=en';
    collectedParameterValues = $(".button--go").attr("results-parameter");
    experienceHire = true;
    createRequestResultSet(collectedParameterValues);

    $(".cmp-opportunity--filter--resultset.studentsandgrads").hide();
    $(".cmp-opportunity--filter--resultset.experienceHire").css("display", "-ms-grid").css("display", "grid");
    $(".accordion--filterby__clear").click();
    $(".cmp-opportunity--filter--resultset").removeClass("active");
    $(".cmp-opportunity--result__set").addClass("cmp-opportunity--result__set--expand");
    $(".accordion--header .accordion--arrow").removeClass("expand");
    $('.accordion--content').hide();
    $(".accordion--jobslevel__label").text("Jobs available in: English");
    $(".accordion--jobslevel__filters .checkbox input#English").prop('checked', true)
    $(".accordion--jobslevel__filters .checkbox input#English-sg").prop('checked', true)

}

function bindAccordioCheckBox() {

    $('.cmp-opportunity--filter__accordion .accordion--header').off('keyup').on('keyup', function (e) {
        if (e.keyCode === 13) {
            $(this).click();
        }
    });
    //Accordion click    
    $('.cmp-opportunity--filter__accordion .accordion--header').off('click').on('click', function (e) {
        $(".cmp-opportunity--filter--resultset").removeClass("active");
        $(".accordion--filter--title, .description_section").hide();
        $(".cmp-opportunity-aggregate .jobcard_arrow").removeClass("up").addClass("down");


        if ($(this).next().is(":visible")) {
            $(this).next().hide();
            $(this).find(".accordion--arrow").removeClass("expand");
            let analyticsVal = $(this).attr("data-analytics-link");
            analyticsVal = analyticsVal.replace("Collapse", "Expand");
            $(this).attr("data-analytics-link", analyticsVal);
            $(".cmp-opportunity--result__set").addClass("cmp-opportunity--result__set--expand");
            return;
        }

        if ($(this).next().next().is(":visible")) {
            $(this).next().hide();
            $(this).next().next().hide();
            $(this).find(".accordion--arrow").removeClass("expand");
            let analyticsVal = $(this).attr("data-analytics-link");
            analyticsVal = analyticsVal.replace("Collapse", "Expand");
            $(this).attr("data-analytics-link", analyticsVal);
            $(".cmp-opportunity--result__set").addClass("cmp-opportunity--result__set--expand");
            return;
        }

        $('.accordion--content').hide();
        $(this).next().next().show();

        if ($(this).next().next().length == 0) {
            $(this).next().show();
        }

        $(this).find(".accordion--arrow").addClass("expand");
        let analyticsVal = $(this).attr("data-analytics-link");
        analyticsVal = analyticsVal.replace("Expand", "Collapse");
        $(this).attr("data-analytics-link", analyticsVal);
        $(".cmp-opportunity--filter--resultset").addClass("active")
        $(".cmp-opportunity--result__set").removeClass("cmp-opportunity--result__set--expand");

    });

    //Checkbox Validation
    $(".accordion--businessarea__wrapper .level_1 input").change(function () {
        var hasChild = $(this).parents(".has-child");
        if (this.checked == false) {
            $(this).parents(".level_1").siblings(".checkbox").find("input")[0].checked = false;
        }
        if ($(hasChild).find(".level_1 input:checked").length == $(hasChild).find(".level_1 input").length) {
            $(this).parents(".level_1").siblings(".checkbox").find("input")[0].checked = true;
        }
        if ($(".accordion--businessarea__wrapper input:checked").length > 0) {
            $(".accordion--businessarea__wrapper .clearSelection").removeClass("disabled");
            $(".accordion--businessarea__wrapper .clearSelection").attr('tabindex', '0');
        } else {
            $(".accordion--businessarea__wrapper .clearSelection").addClass("disabled");
            $(".accordion--businessarea__wrapper .clearSelection").attr('tabindex', '-1');
        }
        let getSelectedLength = $('.accordion--businessarea__wrapper input[type=checkbox]:checked').length;
        if (getSelectedLength > 0) {
            $(".accordion--businessarea__wrapper .selected--checkbox").show().text(" (" + getSelectedLength + " Selected)");
        } else {
            $(".accordion--businessarea__wrapper .selected--checkbox").hide();
        }
    });

    $(".accordion--businessarea__wrapper .no-child input").change(function () {

        if ($(".accordion--businessarea__wrapper input:checked").length > 0) {
            $(".accordion--businessarea__wrapper .clearSelection").removeClass("disabled");
            $(".accordion--businessarea__wrapper .clearSelection").attr('tabindex', '0');
        } else {
            $(".accordion--businessarea__wrapper .clearSelection").addClass("disabled");
            $(".accordion--businessarea__wrapper .clearSelection").attr('tabindex', '-1');
        }
        let getSelectedLength = $('.accordion--businessarea__wrapper input[type=checkbox]:checked').length;
        if (getSelectedLength > 0) {
            $(".accordion--businessarea__wrapper .selected--checkbox").show().text(" (" + getSelectedLength + " Selected)");
        } else {
            $(".accordion--businessarea__wrapper .selected--checkbox").hide();
        }
    });

    $(".accordion--businessarea__wrapper .has-child >div.checkbox input").change(function () {
        var status = this.checked;
        var childNodes = $(this).parents(".has-child").find(".level_1 input");
        $(childNodes).each(function () {
            this.checked = status
        });
        if ($(".accordion--businessarea__wrapper input:checked").length > 0) {
            $(".accordion--businessarea__wrapper .clearSelection").removeClass("disabled");
            $(".accordion--businessarea__wrapper .clearSelection").attr('tabindex', '0');
        } else {
            $(".accordion--businessarea__wrapper .clearSelection").addClass("disabled");
            $(".accordion--businessarea__wrapper .clearSelection").attr('tabindex', '-1');

        }

        let getSelectedLength = $('.accordion--businessarea__wrapper input[type=checkbox]:checked').length;
        if (getSelectedLength > 0) {
            $(".accordion--businessarea__wrapper .selected--checkbox").show().text(" (" + getSelectedLength + " Selected)");
        } else {
            $(".accordion--businessarea__wrapper .selected--checkbox").hide();
        }
    });
    $(".accordion--businessarea__wrapper .clearSelection").on('keyup', function (e) {
        if (e.keyCode === 13) {
            $(this).click();
        }
    })
    $(".accordion--businessarea__wrapper .clearSelection").click(function () {
        $('.accordion--businessarea__wrapper input[type=checkbox]').prop('checked', false);
        $(this).addClass("disabled");
        $(this).attr("tabindex", "-1");
        $(".accordion--businessarea__wrapper .selected--checkbox").hide();

    });

    $(".accordion--programtype__wrapper div.checkbox input").change(function () {

        if ($(".accordion--programtype__wrapper input:checked").length > 0) {
            $(".accordion--programtype__wrapper .clearSelection").removeClass("disabled");
            $(".accordion--programtype__wrapper .clearSelection").attr('tabindex', '0');
        } else {
            $(".accordion--programtype__wrapper .clearSelection").addClass("disabled");
            $(".accordion--programtype__wrapper .clearSelection").attr('tabindex', '-1');
        }

        let getProgramSelectedLength = $('.accordion--programtype__wrapper input[type=checkbox]:checked').length;
        if (getProgramSelectedLength > 0) {
            $(".accordion--programtype__wrapper .selected--checkbox").show().text(" (" + getProgramSelectedLength + " Selected)");
        } else {
            $(".accordion--programtype__wrapper .selected--checkbox").hide();
        }
    });

    $(".accordion--programtype__wrapper .clearSelection").on('keyup', function (e) {
        if (e.keyCode === 13) {
            $(this).click();
        }
    })
    $(".accordion--programtype__wrapper .clearSelection").click(function () {
        $('.accordion--programtype__wrapper input[type=checkbox]').prop('checked', false);
        $(this).addClass("disabled");
        $(this).attr("tabindex", "-1");
        $(".accordion--programtype__wrapper .selected--checkbox").hide();

    });

    $(".accordion--educationlevel__wrapper div.checkbox input").change(function () {

        if ($(".accordion--educationlevel__wrapper input:checked").length > 0) {
            $(".accordion--educationlevel__wrapper .clearSelection").removeClass("disabled");
            $(".accordion--educationlevel__wrapper .clearSelection").attr('tabindex', '0');
        } else {
            $(".accordion--educationlevel__wrapper .clearSelection").addClass("disabled");
            $(".accordion--educationlevel__wrapper .clearSelection").attr('tabindex', '-1');

        }

        let getEducatonSelectedLength = $('.accordion--educationlevel__wrapper input[type=checkbox]:checked').length;
        if (getEducatonSelectedLength > 0) {
            $(".accordion--educationlevel__wrapper .selected--checkbox").show().text(" (" + getEducatonSelectedLength + " Selected)");
        } else {
            $(".accordion--educationlevel__wrapper .selected--checkbox").hide();
        }
    });

    $(".accordion--educationlevel__wrapper .clearSelection").on('keyup', function (e) {
        if (e.keyCode === 13) {
            $(this).click();
        }
    })
    $(".accordion--educationlevel__wrapper .clearSelection").click(function () {
        $('.accordion--educationlevel__wrapper input[type=checkbox]').prop('checked', false);
        $(this).addClass("disabled");
        $(this).attr("tabindex", "-1");
        $(".accordion--educationlevel__wrapper .selected--checkbox").hide();

    });


    // No results Clear all filters
    $(".button--noresults-clearall-ep").off().on('click', function (e) {
        e.preventDefault();
        clearAllFilterExpProf();
    });


    // No results Clear all filters
    $(".button--noresults-clearall-sg").off().on('click', function (e) {
        e.preventDefault();
        clearAllFilterStudGrads()
    });


    // Clear All Filters
    $(".cmp-opportunity--filter__accordion .accordion--filterby__clear").on('keyup', function (e) {
        if (e.keyCode === 13) {
            $(this).click();
        }
    })
    $(".cmp-opportunity--filter__accordion .accordion--filterby__clear").on('click', function (e) {
        e.preventDefault();
        // Clear locations
        $(this).attr('tabindex', '-1');
        let parent = findAncestor(e.target, RESULTSET_CLASS),
            locationFilterSec = parent.querySelector("." + LOCATION_FILTER_CLASS);
        if (getActualLocationSelections(locationFilterSec).length > 0) {
            if (window.innerWidth > 767) {
                clearAllSelections(locationFilterSec);
                initLocationSet();
            } else clearAllMobileSelections(locationFilterSec);
            updateLocationCount(locationFilterSec);
            locationFilterSec.querySelector(".clearSelection").classList.remove("disabled");
        }

        $('.cmp-opportunity--filter__accordion input[type=checkbox]').prop('checked', false);
        $(".accordion--jobslevel__label").text("Jobs available in: English");
        $(".accordion--jobslevel__filters .checkbox input#English").prop('checked', true)
        $(".accordion--jobslevel__filters .checkbox input#English-sg").prop('checked', true)

        $(this).addClass("disabled");
        $(".cmp-opportunity--filter__accordion .selected--checkbox").hide();
        $(".clearSelection").addClass("disabled");

        $(".cmp-opportunity--filter--resultset").addClass("active");
        $(".cmp-opportunity--result__set").removeClass("cmp-opportunity--result__set--expand");
    });

    $(".cmp-opportunity--filter__accordion div.checkbox input").change(function () {

        if ($(".cmp-opportunity--filter__accordion input:checked").length > 0) {
            $(".accordion--filterby__clear").removeClass("disabled");
            $(".accordion--filterby__clear").attr("tabindex", "0");
        } else {
            $(".accordion--educationlevel__wrapper .clearSelection").addClass("disabled");
        }
    });

}

// Create request for result set
function createRequestResultSet(collectedParameterValues, keyword) {
    var pageUrl = window.location.href;
    if (pageUrl.indexOf('career-opportunities-search') > -1) {
        var SERVLET_PATH = window.location.origin + "/web/career_services/webapp/service/careerservice/resultset.json?" + collectedParameterValues;
        resultSet = fetchResultSet(SERVLET_PATH);
        if (resultSet) {
            if (keyword) {
                let filteredResultSet = resultSet;
                let filteredResultArray = searchKeyword(resultSet.resultSet, keyword);
                if (filteredResultArray.length > 0) {
                    filteredResultSet.resultSet = filteredResultArray;
                    filteredResultSet.totalResults = filteredResultArray.length;
                    generateResult(filteredResultSet, currentPage, keyword);
                } else {
                    SERVLET_PATH = SERVLET_PATH + '&location=' + keyword;
                    let resultSetNotfound = fetchResultSet(SERVLET_PATH);
                    if (resultSetNotfound)
                        generateResult(resultSetNotfound, currentPage, keyword);
                }
            } else {
                enteredKeyword = "";
                generateResult(resultSet, currentPage);
            }
            $(".cmp-opportunity--filter__slick input").attr('tabindex', '0');
            accessibilityLocation();
        }
        $(".backButton").hide();
    }
}

function searchKeyword(resultSetArray, keyword) {

    let filteredResult = resultSetArray.filter(function (item) {
        let flag = false;
        /*iterate through individual job(item) keys*/
        Object.keys(item).forEach(function (key, index) {
            /* return once the keyword found -  no need of iterate through all the key*/
            if (flag) {
                return;
            }
            //let ignoreKeyName = ignoreKeysArr.find(ignoreKey => ignoreKey===key);

            let ignoreKeyStatus = isIgnoreKey(key);

            /*check only for the key other than values in ignoreKeysArr*/
            if (!ignoreKeyStatus) {
                if (item[key] && (typeof item[key] == "string" || typeof item[key] == "number")) {
                    if ((item[key].toString().toLowerCase()).indexOf(keyword.toString().toLowerCase()) > -1) {
                        flag = true;
                        return;
                    }
                }
            }
        });
        return flag;
    })
    return filteredResult;
}

function isIgnoreKey(key) {
    const ignoreKeysArr = ["applicationDate", "sortingDate", "jobHtmlDescription", "jobDescription", "url", "learnMoreCta"];
    let result = false;
    ignoreKeysArr.forEach(function (ignoreKey) {
        if (ignoreKey === key) {
            result = true;
            return;
        }
    });
    return result;
}

function getUrlParameter(name) {
    name = name.replace(/[\[]/, '\\[').replace(/[\]]/, '\\]');
    var regex = new RegExp('[\\?&]' + name + '=([^&#]*)');
    var results = regex.exec(location.search);
    return results === null ? '' : decodeURIComponent(results[1].replace(/\+/g, ' '));
};

// Populated selected checkbox values
function getSelectedValues() {
    var collectedValues = "";
    var childValues = "";
    var parentValues = "";
    var tempArray = [];

    function searchURL(nameKey, fillterdArray) {
        for (let i = 0; i < fillterdArray.length; i++) {
            if (fillterdArray[i].name.toLowerCase() === nameKey.toLowerCase()) {
                return fillterdArray[i].url;
            }
        }
    }

    if (experienceHire) {
        var getRegionParam = getUrlParameter('country').toLowerCase();
        locationJsonData = fetchDropdownJson("/content/dam/msdotcom/appdata/filter-metadata-location.json");
        if (getRegionParam) {
            let filterdArrayObj = locationJsonData.filter(item => item.open === "internal");
            var getCountrylength = getRegionParam.split(",");

            getCountrylength.forEach(function (key, index) {
                let getFiltterValue = searchURL(getCountrylength[index].toLowerCase(), filterdArrayObj);

                if (collectedValues == "") {
                    collectedValues = getFiltterValue;
                } else if (getFiltterValue != undefined) {
                    let getFirstQueryUrl = collectedValues.split("&")[2].split("=")[1];
                    let getSecondQueryUrl = getFiltterValue.split("&")[2].split("=")[1];
                    collectedValues = "&opportunity=ep&location=" + getFirstQueryUrl + ";" + getSecondQueryUrl + "&lang=EN";
                }
            });

            if (collectedValues === undefined) {
                collectedValues = "&opportunity=ep&lang=en&location=notfound";
            }

        } else if (opportunityValue == "ep") {
            let filterdArray = locationJsonData.filter(item => item.open === "internal");
            filterdArray.forEach(function (key, index) {
                if (collectedValues == "") {
                    collectedValues = key.url;
                } else {
                    let getFirstQueryUrl = collectedValues.split("&")[2].split("=")[1];
                    let getSecondQueryUrl = key.url.split("&")[2].split("=")[1];
                    collectedValues = "&opportunity=ep&location=" + getFirstQueryUrl + ";" + getSecondQueryUrl + "&lang=EN";
                }
            });
        }
    } else {
        collectedValues = '&opportunity=' + opportunityValue;
    }


    var regionQueryString = [];
    $("[data-region-name]").each(function (index, val) {

        var region = val.getAttribute("data-region-name");
        var hasChild = $(val).find(".location-dropdown");
        if (region && hasChild.length > 0) {

            $(hasChild).each(function (index, subLocation) {

                var country = $(subLocation).find("[name='country']") && $(subLocation).find("[name='country']").val().indexOf("select-any") != 0 ? "_" + $(subLocation).find("[name='country']").val() : ""
                var state = $(subLocation).find("[name='state']") && $(subLocation).find("[name='state']").val().indexOf("all") != 0 ? "_" + $(subLocation).find("[name='state']").val() : ""
                var city = $(subLocation).find("[name='city']") && $(subLocation).find("[name='city']").val().indexOf("all") != 0 ? ":" + $(subLocation).find("[name='city']").val() : ""

                if (country != "") {
                    if (country == "_all") {
                        country = "";
                    }
                    regionQueryString.push(region + country + state + city);
                }

            });
        }

    })

    if (regionQueryString.length > 0)
        collectedValues += "&location=" + regionQueryString.join(';');


    $(".accordion--businessarea__wrapper .has-child > div.checkbox input").each(function () {
        if (this.checked) {
            tempArray.push($(this).val());
            if (tempArray.length !== 0 && parentValues.indexOf("businessArea") > -1) {
                parentValues += ';' + tempArray.toString();
                tempArray = [];
            } else {
                parentValues += '&businessArea=' + tempArray.toString();
                tempArray = [];
            }
        } else {
            var childNodes = $(this).parents(".has-child").find(".level_1 input:checked");
            $(childNodes).each(function () {
                tempArray.push($(this).val());

                if (tempArray.length !== 0 && childValues.indexOf("division") > -1) {
                    childValues += ';' + tempArray.toString();
                    tempArray = [];
                } else if (tempArray.length !== 0) {
                    childValues += '&division=' + tempArray.toString();
                    tempArray = [];
                }
            });

        }

    })

    $(".accordion--businessarea__filters .no-child > div.checkbox input").each(function () {
        if (this.checked) {
            tempArray.push($(this).val());
            if (tempArray.length !== 0 && parentValues.indexOf("businessArea") > -1) {
                parentValues += ';' + tempArray.toString();
                tempArray = [];
            } else {
                parentValues += '&businessArea=' + tempArray.toString();
                tempArray = [];
            }
        }
    });

    if (parentValues.length > 0 || childValues.length > 0) {
        collectedValues += parentValues + childValues;
    }


    $($(".accordion--educationlevel__filters li").find("input:checked")).each(function () {
        tempArray.push($(this).val());
    })
    if (tempArray.length !== 0) {
        collectedValues += '&educationLevel=' + tempArray.join(";");
        tempArray = [];
    }

    $($(".accordion--programtype__filters li").find("input:checked")).each(function () {
        tempArray.push($(this).val());
    })
    if (tempArray.length !== 0) {
        collectedValues += '&empType=' + tempArray.join(";");
        tempArray = [];
    }


    if (experienceHire) {
        tempArray.push($($(".experienceHire .accordion--jobslevel__filters li").find("input:checked")).val());
    } else {
        tempArray.push($($(".studentsandgrads .accordion--jobslevel__filters li").find("input:checked")).val());
    }


    if (tempArray.length !== 0) {
        collectedValues += '&lang=' + tempArray.join(";");
        tempArray = [];
    }

    return collectedValues;
}

